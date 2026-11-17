"""批次服务（V2 批次内核 + S3 检验放行/追溯）。

- 批次号全表唯一；同批次号再入库复用原记录（须同物料，异物料 400）。
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

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import (
    Batch,
    InspStatus,
    InspectionRecord,
    InventoryMove,
    ManufacturingOrder,
    MoveType,
)
from erp_lite.services.inventory import get_stock, q4


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
    """按批次号取批次；不存在则创建，存在则复用（校验同物料）。

    返回 (批次, 是否新建)。复用时保留原来源/供应商/检验状态。
    """
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is not None:
        if batch.material_id != material_id:
            raise ValueError(
                f"批次号 {batch_no} 已属于物料 {batch.material_id}，"
                f"不能用于物料 {material_id}"
            )
        return batch, False
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


# ---------- S3：放行拦截 / 检验判定 / 不合格处置 ----------


def ensure_batch_usable(batch: Batch) -> None:
    """放行拦截：仅 qualified 批次可被 MO 领料 / SO 出库（S3 强制口径）。

    pending / rejected / closed 一律 ValueError（路由层转 400），提示区分：
    待检未放行 / 不合格已隔离 / 已关闭。
    """
    if batch.insp_status == InspStatus.qualified:
        return
    if batch.insp_status == InspStatus.pending:
        raise ValueError(
            f"批次 {batch.batch_no} 待检验，QA 放行后方可领料/出库"
        )
    if batch.insp_status == InspStatus.rejected:
        raise ValueError(
            f"批次 {batch.batch_no} 检验不合格，已隔离，禁止领料/出库"
        )
    raise ValueError(f"批次 {batch.batch_no} 已关闭，禁止领料/出库")


def inspect_batch(
    db: Session,
    batch: Batch,
    *,
    items: list[dict],
    conclusion: str,
    inspector: str | None = None,
    remark: str | None = None,
) -> InspectionRecord:
    """录入检验记录并判定：pass → qualified，fail → rejected；closed 批次拒绝。

    一次判定一条记录（历史保留，支持复判），由调用方写审计/日志并提交。
    """
    if batch.insp_status == InspStatus.closed:
        raise ValueError(f"批次 {batch.batch_no} 已关闭，不可再检验判定")
    record = InspectionRecord(
        batch_id=batch.id,
        items_json=items,
        conclusion=conclusion,
        inspector=inspector,
        remark=remark,
    )
    db.add(record)
    batch.insp_status = (
        InspStatus.qualified if conclusion == "pass" else InspStatus.rejected
    )
    return record


def dispose_batch(
    db: Session, batch: Batch, *, disposition: str, remark: str | None = None
) -> Batch:
    """不合格批次处置（return 退货 / destroy 销毁）→ insp_status=closed 留痕。

    仅 rejected 批次可处置；closed 不可重复处置，pending/qualified 拒绝。
    """
    if batch.insp_status != InspStatus.rejected:
        raise ValueError(
            f"批次 {batch.batch_no} 检验状态为 {batch.insp_status.value}，"
            f"仅检验不合格（rejected）的批次可处置"
        )
    batch.disposition = disposition
    batch.disposition_remark = remark
    batch.insp_status = InspStatus.closed
    return batch


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
