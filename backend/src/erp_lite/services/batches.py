"""批次服务（V2 批次内核 + S3 检验放行/追溯）。

- 批次号全表唯一；同批次号再入库仅当原批次 insp_status=pending 时复用
  （追加数量仍待检；须同物料，异物料或非 pending 状态 400）。
- 采购入库批次 insp_status=pending（待检）；MO 完工批次建为 qualified
  （成品默认合格，后续可由 QA 复判）。
- 放行控制（S3 强制）：仅 qualified 批次可被 MO 领料 / SO 出库，
  pending/rejected/closed 一律 400（ensure_batch_usable，统一收紧）。
- 可用量口径：get_available(only_qualified=True) 供 MRP 使用
  （批次物料只计 qualified 批次）；默认参数保留 S2 行为供对照查询。
- 正反向追溯：trace_forward（成品/半成品 → 所耗原料/子件批次+供应商）、
  trace_backward（原料/子件批次 → 流向的成品批次）。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.models import (
    Batch,
    InspStatus,
    InspectionRecord,
    InventoryMove,
    ManufacturingOrder,
    MoveType,
)
from erp_lite.services.inventory import get_stock, post_move, q4


def get_or_create_batch(
    db: Session,
    *,
    batch_no: str,
    material_id: int,
    source_type: str,
    source_id: int,
    supplier_id: int | None = None,
    insp_status: InspStatus = InspStatus.pending,
    created_by: str | None = None,
) -> tuple[Batch, bool]:
    """按批次号取批次；不存在则创建。v3：批次号全表唯一且**不复用**——

    已存在的批次号再入库一律 ValueError（路由层转 400，提示换号），
    防止第二张 PO 的收货/应付挂到第一张 PO 的批次来源上（来源失真）。
    """
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is not None:
        raise ValueError(
            f"批次号 {batch_no} 已存在（批次 #{batch.id}），批次号不复用，请使用新批次号"
        )
    batch = Batch(
        batch_no=batch_no,
        material_id=material_id,
        source_type=source_type,
        source_id=source_id,
        supplier_id=supplier_id,
        insp_status=insp_status,
        created_by=created_by,
    )
    db.add(batch)
    db.flush()
    return batch, True


def next_finished_batch_no(db: Session, mo_id: int, mo_doc_no: str) -> str:
    """成品批次号 = MO 单号 + '-' + 序号（按该 MO 已生成批次数递增）。"""
    count = len(
        db.scalars(
            select(Batch.id).where(
                Batch.source_type == "manufacturing_order",
                Batch.source_id == mo_id,
            )
        ).all()
    )
    return f"{mo_doc_no}-{count + 1}"


def get_batch_stock(
    db: Session, batch_id: int, warehouse_id: int | None = None
) -> tuple[Decimal, Decimal]:
    """批次现存量 / 现存金额 = 该批次流水汇总（入库正、出库负）。"""
    stmt = select(Batch.material_id).where(Batch.id == batch_id)
    material_id = db.scalar(stmt)
    if material_id is None:
        return q4(0), q4(0)
    return get_stock(db, material_id, warehouse_id, batch_id=batch_id)


def get_available(
    db: Session,
    material_id: int,
    warehouse_id: int | None = None,
    only_qualified: bool = False,
) -> Decimal:
    """可用量 = Σ 批次现存（only_qualified=True 时只计 qualified 批次）。

    S2 口径：默认含 pending（物理在库即可见）；S3 接入检验放行后，
    领料/出库校验与 MRP 可用量切 only_qualified=True。
    """
    stmt = select(Batch.id).where(Batch.material_id == material_id)
    if only_qualified:
        stmt = stmt.where(Batch.insp_status == InspStatus.qualified)
    total = Decimal("0")
    for batch_id in db.scalars(stmt).all():
        qty, _ = get_stock(db, material_id, warehouse_id, batch_id=batch_id)
        total = q4(total + qty)
    return total


# ---------- S3：放行拦截 / v3 双段检验（qc 录入 → qa 判定）/ 不合格处置 ----------


def ensure_batch_usable(batch: Batch) -> None:
    """放行拦截：仅 qualified 批次可被 MO 领料 / SO 出库（S3 强制口径）。

    pending / rejected / closed 一律 ValueError（路由层转 400），提示区分：
    待检未放行 / 不合格已隔离 / 已关闭。
    """
    if batch.insp_status == InspStatus.qualified:
        return
    if batch.insp_status == InspStatus.pending:
        raise ValueError(
            f"批次 {batch.batch_no} 待检验（qc 录入 → qa 放行），放行后方可领料/出库"
        )
    if batch.insp_status == InspStatus.rejected:
        raise ValueError(
            f"批次 {batch.batch_no} 检验不合格，已隔离，禁止领料/出库"
        )
    raise ValueError(f"批次 {batch.batch_no} 已关闭，禁止领料/出库")


def record_inspection(
    db: Session,
    batch: Batch,
    *,
    items: list[dict],
    suggest_conclusion: str,
    recorder: str | None = None,
    remark: str | None = None,
) -> InspectionRecord:
    """qc 段：录入检项结果与建议结论；批次保持 pending，等待 qa 复核判定。

    同一批次存在未判定记录时拒绝重复录入（先让 qa 判定）；closed 批次拒绝。
    判定（judge_batch）由 qa 在同一条记录上完成，judge≠recorder 由路由层校验。
    """
    if batch.insp_status == InspStatus.closed:
        raise ValueError(f"批次 {batch.batch_no} 已关闭，不可再检验")
    pending_rec = db.scalar(
        select(InspectionRecord)
        .where(
            InspectionRecord.batch_id == batch.id,
            InspectionRecord.conclusion.is_(None),
        )
        .order_by(InspectionRecord.id.desc())
    )
    if pending_rec is not None:
        raise ValueError(
            f"批次 {batch.batch_no} 已有待判定检验记录（录入人 {pending_rec.recorder}），"
            f"请先由 qa 完成复核判定"
        )
    record = InspectionRecord(
        batch_id=batch.id,
        items_json=items,
        suggest_conclusion=suggest_conclusion,
        recorder=recorder,
        remark=remark,
    )
    db.add(record)
    db.flush()
    return record


def judge_batch(
    db: Session, batch: Batch, *, conclusion: str, judge_by: str
) -> InspectionRecord:
    """qa 段：复核判定最近一条未判定记录；pass → qualified / fail → rejected。

    无未判定记录时 400（须先由 qc 录入）；自审拦截（judge≠recorder）由路由层
    ensure_not_self 校验。历史记录保留，支持复判（复判走新的录入-判定循环）。
    """
    if batch.insp_status == InspStatus.closed:
        raise ValueError(f"批次 {batch.batch_no} 已关闭，不可再判定")
    record = db.scalar(
        select(InspectionRecord)
        .where(
            InspectionRecord.batch_id == batch.id,
            InspectionRecord.conclusion.is_(None),
        )
        .order_by(InspectionRecord.id.desc())
    )
    if record is None:
        raise ValueError(
            f"批次 {batch.batch_no} 暂无待判定检验记录，请先由 qc 录入检验记录"
        )
    record.conclusion = conclusion
    record.judge_by = judge_by
    batch.insp_status = (
        InspStatus.qualified if conclusion == "pass" else InspStatus.rejected
    )
    return record


def scrap_batch(db: Session, batch: Batch, *, actor: str | None = None) -> Decimal:
    """报废清库存（v3 处置闭环 destroy 分支）：按批次现存逐仓写负向 adjust 流水，
    loss_amount = 冲减的批次现存金额。返回损失金额。

    成品复判不合格与原料报废共用：库存价值不再残留（修 v2 断头路）；
    已结转的生产成本不回冲（MO transferred_cost 不动），损失在批次上留痕。
    """
    rows = (
        db.execute(
            select(
                InventoryMove.warehouse_id,
                func.coalesce(func.sum(InventoryMove.qty), 0),
                func.coalesce(func.sum(InventoryMove.amount), 0),
            )
            .where(InventoryMove.batch_id == batch.id)
            .group_by(InventoryMove.warehouse_id)
        )
        .all()
    )
    loss = Decimal("0")
    for warehouse_id, qty, amount in rows:
        qty_d, amount_d = q4(qty), q4(amount)
        if qty_d <= 0:
            continue
        post_move(
            db,
            material_id=batch.material_id,
            warehouse_id=warehouse_id,
            move_type=MoveType.adjust,
            qty=-qty_d,
            unit_cost=q4(amount_d / qty_d),
            source_type="batch_dispose",
            source_id=batch.id,
            batch_id=batch.id,
        )
        loss = q4(loss + amount_d)
    batch.loss_amount = q4(batch.loss_amount + loss)
    return loss


def dispose_close(
    batch: Batch, *, disposition: str, remark: str | None = None
) -> None:
    """处置状态收尾：写 disposition 并置 closed（账务联动由调用方完成后调用）。

    仅 rejected 批次可处置（v2 口径保留）：pending/qualified 未判不合格、
    closed 已处置，一律 ValueError（路由层转 400）。
    """
    if batch.insp_status != InspStatus.rejected:
        raise ValueError(
            f"批次 {batch.batch_no} 检验状态为 {batch.insp_status.value}，"
            f"仅检验不合格（rejected）的批次可处置"
        )
    batch.disposition = disposition
    batch.disposition_remark = remark
    batch.insp_status = InspStatus.closed


# ---------- S3：批次正反向追溯 ----------


def _batch_brief(batch: Batch) -> dict[str, Any]:
    """追溯 link 里的批次摘要（不引入 pydantic，服务层保持纯 ORM）。"""
    return {
        "id": batch.id,
        "batch_no": batch.batch_no,
        "material_id": batch.material_id,
        "insp_status": batch.insp_status.value,
        "source_type": batch.source_type,
        "source_id": batch.source_id,
        "supplier_id": batch.supplier_id,
    }


def trace_forward(db: Session, batch: Batch) -> dict[str, Any]:
    """正向追溯：成品/半成品批次 → （递归）它生产时领用掉的批次+供应商。

    经 MO 领料流水（mo_issue 且带 batch_id）逐层下钻；原料批次
    （source_type=purchase_order）无生产下游，方向不当报错。
    links: [{via_doc(MO 单号), to_batch, partner(原料批次供应商名), qty(领用量)}]
    """
    if batch.source_type != "manufacturing_order":
        raise ValueError(
            f"批次 {batch.batch_no} 为采购/原料批次，无生产下游，"
            f"正向追溯方向不当，请使用反向追溯"
        )
    links: list[dict[str, Any]] = []
    visited: set[int] = set()

    def expand(current: Batch) -> None:
        if current.source_type != "manufacturing_order" or current.id in visited:
            return
        visited.add(current.id)
        moves = db.scalars(
            select(InventoryMove).where(
                InventoryMove.source_type == "manufacturing_order",
                InventoryMove.source_id == current.source_id,
                InventoryMove.move_type == MoveType.mo_issue,
                InventoryMove.batch_id.isnot(None),
            )
        ).all()
        mo = db.get(ManufacturingOrder, current.source_id)
        via_doc = mo.doc_no if mo is not None else f"MO#{current.source_id}"
        for move in moves:
            used = db.get(Batch, move.batch_id)
            if used is None:
                continue
            partner = used.supplier.name if used.supplier is not None else None
            links.append(
                {
                    "via_doc": via_doc,
                    "to_batch": _batch_brief(used),
                    "partner": partner,
                    "qty": q4(-move.qty),  # 领料流水为负，追溯取绝对用量
                }
            )
            expand(used)  # 半成品批次继续向下层展开（如 SEMI → RAW）

    expand(batch)
    return {"batch": _batch_brief(batch), "links": links}


def trace_backward(db: Session, batch: Batch) -> dict[str, Any]:
    """反向召回：原料/子件批次 → 经 mo_issue 流水逐层找到流向的成品批次。

    RAW 被 MO1 领用产 SEMI、SEMI 又被 MO2 领用产 FG 时，从 RAW 反查
    同时给出（MO1→SEMI 批次）与（MO2→FG 批次）两层链路。
    根批次无任何领料流水（成品批次）说明方向不当报错。
    注意：反向追溯为 MO 级粒度过包归因（保守召回口径）——同一 MO 产出的
    所有成品批次都会被召回，不按批次内实际消耗精确分摊。
    links: [{via_doc(领料 MO 单号), to_batch(下游成品/半成品批次), partner, qty(领用量)}]
    """
    links: list[dict[str, Any]] = []
    visited: set[int] = set()

    def expand(current: Batch) -> None:
        if current.id in visited:
            return
        visited.add(current.id)
        issue_moves = db.scalars(
            select(InventoryMove).where(
                InventoryMove.batch_id == current.id,
                InventoryMove.move_type == MoveType.mo_issue,
            )
        ).all()
        if not issue_moves:
            if current.id == batch.id:
                raise ValueError(
                    f"批次 {batch.batch_no} 无生产领料流向，反向追溯方向不当，"
                    f"请使用正向追溯"
                )
            return  # 终层成品批次，不再向上
        for move in issue_moves:
            mo = db.get(ManufacturingOrder, move.source_id)
            via_doc = mo.doc_no if mo is not None else f"MO#{move.source_id}"
            receipts = db.scalars(
                select(InventoryMove).where(
                    InventoryMove.source_type == "manufacturing_order",
                    InventoryMove.source_id == move.source_id,
                    InventoryMove.move_type == MoveType.mo_receipt,
                    InventoryMove.batch_id.isnot(None),
                )
            ).all()
            for receipt in receipts:
                produced = db.get(Batch, receipt.batch_id)
                if produced is None:
                    continue
                links.append(
                    {
                        "via_doc": via_doc,
                        "to_batch": _batch_brief(produced),
                        "partner": None,
                        "qty": q4(-move.qty),
                    }
                )
                expand(produced)

    expand(batch)
    return {"batch": _batch_brief(batch), "links": links}
