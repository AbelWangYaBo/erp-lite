"""生产工单 MO：建单/下达(approve/reject/void)/领料(issue/unissue)/完工入库(receive)/关闭(close)。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import ensure_not_self, get_current_user, require_action
from erp_lite.models import (
    Batch,
    DocStatus,
    InventoryMove,
    ManufacturingOrder,
    Material,
    MoIssue,
    User,
    Warehouse,
)
from erp_lite.models.base import InspStatus, MoveType
from erp_lite.routers.common import change_status, paginate
from erp_lite.schemas.production import (
    MoCreate,
    MoDetail,
    MoIssueIn,
    MoOut,
    MoReceiveIn,
    MoUnissueIn,
)
from erp_lite.schemas.orders import RejectIn
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.batches import ensure_batch_usable, get_or_create_batch, next_finished_batch_no
from erp_lite.services.inventory import _d, get_stock, post_move, q4
from erp_lite.services.mrp import expand_requirements
from erp_lite.services.numbering import next_doc_no

router = APIRouter(
    prefix="/api/v1/manufacturing-orders",
    tags=["manufacturing"],
    dependencies=[Depends(get_current_user)],
)

DOC_TYPE = "manufacturing_order"
ZERO = Decimal("0")


def _get_or_404(db: Session, mo_id: int) -> ManufacturingOrder:
    mo = db.get(ManufacturingOrder, mo_id)
    if mo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="生产工单不存在"
        )
    return mo


def _require_status(mo: ManufacturingOrder, allowed: tuple[DocStatus, ...], action: str) -> None:
    if mo.status not in allowed:
        names = "、".join(s.value for s in allowed)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仅 {names} 状态可{action}",
        )


@router.get("", response_model=None)
def list_mos(
    keyword: str | None = None,
    status_filter: DocStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(ManufacturingOrder).order_by(ManufacturingOrder.id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(ManufacturingOrder.doc_no.ilike(like), ManufacturingOrder.remark.ilike(like))
        )
    if status_filter is not None:
        stmt = stmt.where(ManufacturingOrder.status == status_filter)
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [MoOut.model_validate(m).model_dump(mode="json") for m in items],
        "total": total,
    }


@router.get("/{mo_id}", response_model=MoDetail)
def get_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    return _get_or_404(db, mo_id)


@router.post("", response_model=MoOut, dependencies=[Depends(require_action("mo.create"))])
def create_mo(
    payload: MoCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    if db.get(Material, payload.material_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"物料不存在：{payload.material_id}",
        )
    if db.get(Warehouse, payload.warehouse_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"仓库不存在：{payload.warehouse_id}",
        )
    mo = ManufacturingOrder(
        doc_no=next_doc_no(db, "MO"),
        material_id=payload.material_id,
        planned_qty=_d(payload.planned_qty),
        warehouse_id=payload.warehouse_id,
        remark=payload.remark,
        created_by=current_user.username,
    )
    db.add(mo)
    db.flush()
    write_audit(db, DOC_TYPE, mo.id, "create", current_user.username,
                role=current_user.role, doc_no=mo.doc_no)
    write_op_log(db, current_user.username, "manufacturing_order_create",
                 f"新增生产工单：{mo.doc_no}", doc_type=DOC_TYPE, doc_id=mo.id)
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/approve", response_model=MoOut, dependencies=[Depends(require_action("mo.release"))])
def approve_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.draft,), "审核（下达）")
    # 规则 A｜不得自审（DESIGN-v3 §2）
    ensure_not_self(current_user, mo.created_by, "生产工单下达")
    mo.reject_reason = None
    change_status(db, mo, DOC_TYPE, DocStatus.approved, current_user.username,
                  f"下达生产工单：{mo.doc_no}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/reject", response_model=MoOut, dependencies=[Depends(require_action("mo.reject"))])
def reject_mo(
    mo_id: int,
    body: RejectIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    """下达拒绝：approved → draft（退回修改），原因留痕。"""
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved,), "拒绝（退回草稿）")
    ensure_not_self(current_user, mo.created_by, "生产工单审批")
    if any(_d(l.qty) for l in mo.issues):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="已有领料记录，禁止拒绝退回（请走关闭流程）",
        )
    mo.reject_reason = body.reason
    change_status(db, mo, DOC_TYPE, DocStatus.draft, current_user.username,
                  f"拒绝生产工单：{mo.doc_no}，原因：{body.reason}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/void", response_model=MoOut, dependencies=[Depends(require_action("mo.void"))])
def void_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    """草稿作废（v3 补死路）：draft 状态可直接作废。"""
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.draft,), "作废")
    change_status(db, mo, DOC_TYPE, DocStatus.void, current_user.username,
                  f"作废生产工单：{mo.doc_no}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/unissue", response_model=MoDetail, dependencies=[Depends(require_action("mo.unissue"))])
def unissue_mo(
    mo_id: int,
    body: MoUnissueIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    """领料退回（v3）：按 (物料, 批次) 退回指定数量（红字领料，带批次）。

    退回量 ≤ 该 MO 对该 (物料, 批次) 累计领料净量；成本按原领料流水单价冲回，
    issued_qty_cost 同步冲减；批次现存回增（qualified 校验放行原批次的库存修正口径
    不适用——退回是账务修正，不重检）。
    """
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial), "领料退回")
    material = db.get(Material, body.material_id)
    if material is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="物料不存在")
    if material.batch_required and body.batch_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"物料 {material.code} 已启用批次管理，退回必须指定批次",
        )
    # 净领量 = 领料正流水 - 退回负流水（按物料+批次）
    def _net(move_type: "MoveType") -> Decimal:
        total = db.scalar(
            select(func.sum(InventoryMove.qty)).where(
                InventoryMove.source_type == DOC_TYPE,
                InventoryMove.source_id == mo.id,
                InventoryMove.material_id == body.material_id,
                InventoryMove.batch_id == body.batch_id,
                InventoryMove.move_type == move_type,
            )
        )
        return q4(total or 0)

    issued_qty = _net(MoveType.mo_issue)
    returned_qty = _net(MoveType.mo_issue_return)
    net = q4(issued_qty - returned_qty)
    if _d(body.qty) > net:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"退回量超限：该(物料,批次)累计净领料 {net}，本次退回 {body.qty}",
        )
    # 原领料单价：最近一条领料流水的 unit_cost（退回按原价冲回）
    last_issue = db.scalar(
        select(InventoryMove)
        .where(
            InventoryMove.source_type == DOC_TYPE,
            InventoryMove.source_id == mo.id,
            InventoryMove.material_id == body.material_id,
            InventoryMove.batch_id == body.batch_id,
            InventoryMove.move_type == MoveType.mo_issue,
        )
        .order_by(InventoryMove.id.desc())
    )
    unit_cost = q4(last_issue.unit_cost) if last_issue is not None else q4(0)
    move = post_move(
        db,
        material_id=body.material_id,
        warehouse_id=mo.warehouse_id,
        move_type=MoveType.mo_issue_return,
        qty=body.qty,  # 正数入库
        unit_cost=unit_cost,
        source_type=DOC_TYPE,
        source_id=mo.id,
        batch_id=body.batch_id,
    )
    cost = q4(move.amount)  # 退回金额为正
    db.add(MoIssue(mo_id=mo.id, material_id=body.material_id, qty=-_d(body.qty),
                   cost_amount=-cost))
    mo.issued_qty_cost = q4(_d(mo.issued_qty_cost) - cost)
    write_audit(db, DOC_TYPE, mo.id, "unissue", current_user.username,
                role=current_user.role, doc_no=mo.doc_no,
                field="issued_qty_cost",
                old=str(q4(_d(mo.issued_qty_cost) + cost)),
                new=str(q4(mo.issued_qty_cost)))
    write_op_log(db, current_user.username, "manufacturing_order_unissue",
                 f"领料退回：{mo.doc_no} 物料 {body.material_id}"
                 f"批次 {body.batch_id or '-'} × {body.qty}，冲回成本 {cost}",
                 doc_type=DOC_TYPE, doc_id=mo.id)
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/issue", response_model=MoDetail, dependencies=[Depends(require_action("mo.issue"))])
def issue_mo(
    mo_id: int,
    body: MoIssueIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial), "领料")
    planned = _d(mo.planned_qty)
    if body.lines:
        # 指定行：物料必须存在（引用校验）；指定 batch_id 的行视为“整批领用”
        # （v2-S6：中间件如 SEMI 自带 BOM 时仍可按批次直领，不再向下展开）；
        # 未指定 batch_id 的行按 BOM 递归展开，无 BOM 直接采用。
        # 键为 (material_id, batch_id)：逐行按物料+批次独立扣减出库，行间不去重
        # 不合并——同物料同批次多行可合并，不同批次严禁合并（否则批次流水错账）。
        requirements: dict[tuple[int, int | None], Decimal] = {}
        for line in body.lines:
            if db.get(Material, line.material_id) is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"物料不存在：{line.material_id}",
                )
            if line.batch_id is not None:
                key = (line.material_id, line.batch_id)
                requirements[key] = q4(requirements.get(key, ZERO) + _d(line.qty))
            else:
                for mid, qty in expand_requirements(
                    db, line.material_id, _d(line.qty)
                ).items():
                    key = (mid, None)
                    requirements[key] = q4(requirements.get(key, ZERO) + qty)
    else:
        # 按 BOM × 本次完工比例展开；qty 缺省=整单计划量
        ratio_qty = _d(body.qty) if body.qty is not None else planned
        try:
            expanded = expand_requirements(db, mo.material_id, ratio_qty)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            )
        if mo.material_id in expanded:
            # 展开结果含成品自身（无 BOM 成品）时报错：领料对象不能是成品本身
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该物料无 BOM，请通过 lines 指定领料明细",
            )
        requirements = {(mid, None): qty for mid, qty in expanded.items()}
    if not requirements:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="领料明细不能为空"
        )

    # v2 批次：行指定 batch_id 时（该行为叶子物料）挂到对应物料；
    # 批次物料最终没有批次的一律 400（BOM 展开的批次子件必须逐行指定批次）；
    # 每个批次独立校验存在性/归属/放行状态（v2-S3）与批次级现存
    for (mid, batch_id), qty in requirements.items():
        material = db.get(Material, mid)
        if material.batch_required and batch_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"物料 {material.code} 已启用批次管理，领料必须指定批次",
            )
        if batch_id is not None:
            batch = db.get(Batch, batch_id)
            if batch is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"批次不存在：{batch_id}",
                )
            if batch.material_id != mid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"批次 {batch.batch_no} 不属于物料 {mid}，禁止领料",
                )
            # v2-S3 放行拦截：仅 qualified 批次可领料（pending/rejected/closed 400）
            try:
                ensure_batch_usable(batch)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                )
        stock_qty, _ = get_stock(db, mid, mo.warehouse_id, batch_id=batch_id)
        if stock_qty < qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"库存不足：物料 {mid} 现存量 {stock_qty}，需领料 {qty}",
            )
    total_cost = ZERO
    for (mid, batch_id), qty in requirements.items():
        move = post_move(
            db,
            material_id=mid,
            warehouse_id=mo.warehouse_id,
            move_type="mo_issue",
            qty=-qty,
            source_type=DOC_TYPE,
            source_id=mo.id,
            batch_id=batch_id,
        )
        cost = q4(-move.amount)  # 出库金额为负，成本取绝对值
        # MoIssue 无批次维度：同物料多批次时按 (物料, 批次) 逐条落账，
        # 合计仍等于本次领料总成本
        db.add(MoIssue(mo_id=mo.id, material_id=mid, qty=qty, cost_amount=cost))
        total_cost = q4(total_cost + cost)
    mo.issued_qty_cost = q4(_d(mo.issued_qty_cost) + total_cost)
    write_audit(db, DOC_TYPE, mo.id, "issue", current_user.username,
                role=current_user.role, doc_no=mo.doc_no,
                field="issued_qty_cost",
                old=str(_d(mo.issued_qty_cost) - total_cost),
                new=str(_d(mo.issued_qty_cost)))
    write_op_log(db, current_user.username, "manufacturing_order_issue",
                 f"生产领料：{mo.doc_no}，本次领料成本 {total_cost}",
                 doc_type=DOC_TYPE, doc_id=mo.id)
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/receive", response_model=MoOut, dependencies=[Depends(require_action("mo.receive"))])
def receive_mo(
    mo_id: int,
    body: MoReceiveIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial), "完工入库")
    planned = _d(mo.planned_qty)
    completed = q4(mo.completed_qty)  # v3：持久化列（修「UI 完工量恒 0」断点）
    qty = _d(body.qty)
    if q4(_d(mo.issued_qty_cost)) <= 0 and not mo.issues:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该工单尚未领料，禁止零领料完工（先领料或作废工单）",
        )
    if completed + qty > planned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"累计完工将超过计划量：已完工 {completed}，计划 {planned}，本次 {qty}",
        )
    ratio = qty / planned
    extra_cost = _d(body.extra_cost)
    # 成本结转：本次结转额 = min(累计领料成本×完工比例, 未结转余额)。
    # 以 transferred_cost 记录已结转额，交错领料/完工时不重复结转、不漏结转。
    issued = _d(mo.issued_qty_cost)
    available = q4(issued - _d(mo.transferred_cost))
    proportional = q4(issued * ratio)
    transferred = min(proportional, available)
    # 入库单价 =（本次结转领料成本 + 本次制费）/ 本次入库数量
    unit_cost = q4((transferred + extra_cost) / qty)
    # v3 规则 B：成品批次 pending（qc 录入 → qa 判定放行后方可 SO 出库），
    # 不再默认 qualified——堵「完工即合格」漏洞，与原料检验链路统一。
    product = db.get(Material, mo.material_id)
    receipt_batch_id = None
    new_batch = None
    if product.batch_required:
        batch_no = next_finished_batch_no(db, mo.id, mo.doc_no)
        try:
            new_batch, _ = get_or_create_batch(
                db,
                batch_no=batch_no,
                material_id=mo.material_id,
                source_type=DOC_TYPE,
                source_id=mo.id,
                supplier_id=None,
                insp_status=InspStatus.pending,  # v3 规则 B：成品也走双段检验
                created_by=current_user.username,
            )
        except ValueError as exc:
            # 成品批次号撞上已判定的既有批次号（数据异常）→ 400 而非 500
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            )
        receipt_batch_id = new_batch.id
    post_move(
        db,
        material_id=mo.material_id,
        warehouse_id=mo.warehouse_id,
        move_type="mo_receipt",
        qty=qty,
        unit_cost=unit_cost,
        source_type=DOC_TYPE,
        source_id=mo.id,
        batch_id=receipt_batch_id,
    )
    mo.transferred_cost = q4(_d(mo.transferred_cost) + transferred)
    mo.extra_cost = q4(_d(mo.extra_cost) + extra_cost)
    mo.received_qty = q4(_d(mo.received_qty) + qty)
    mo.completed_qty = q4(completed + qty)
    if new_batch is not None:
        # v3：批次创建留痕
        write_audit(
            db, "batch", new_batch.id, "create", current_user.username,
            role=current_user.role, doc_no=new_batch.batch_no,
            field="source", new=f"manufacturing_order#{mo.id}({mo.doc_no})",
        )
    next_hint = f"，批次 {new_batch.batch_no} 待检（qc 录入 → qa 判定）" if new_batch else ""
    new_status = DocStatus.completed if completed + qty >= planned else DocStatus.partial
    if new_status != mo.status:
        change_status(db, mo, DOC_TYPE, new_status, current_user.username,
                      f"完工入库：{mo.doc_no} 本次 {qty}，单价 {unit_cost}{next_hint}")
    else:
        write_audit(db, DOC_TYPE, mo.id, "receive", current_user.username,
                    role=current_user.role, doc_no=mo.doc_no,
                    field="completed_qty", old=str(completed), new=str(completed + qty))
        write_op_log(db, current_user.username, "manufacturing_order_receive",
                     f"完工入库：{mo.doc_no} 本次 {qty}，单价 {unit_cost}{next_hint}",
                     doc_type=DOC_TYPE, doc_id=mo.id)
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/close", response_model=MoOut, dependencies=[Depends(require_action("mo.close"))])
def close_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial, DocStatus.completed),
                    "关闭")
    issued = _d(mo.issued_qty_cost)
    transferred = _d(mo.transferred_cost)
    remaining = q4(issued - transferred)
    completed = q4(mo.completed_qty)
    if remaining > 0 and completed > 0:
        # 部分完工关闭：剩余成本按各完工批次的完工量比例拆分，
        # 逐批次写 qty=0 纯金额 adjust 流水（v3 修复批次/物料均价分叉）。
        receipts = db.scalars(
            select(InventoryMove).where(
                InventoryMove.source_type == DOC_TYPE,
                InventoryMove.source_id == mo.id,
                InventoryMove.move_type == MoveType.mo_receipt,
                InventoryMove.batch_id.isnot(None),
            )
        ).all()
        if receipts:
            total_receipt = q4(sum(_d(r.qty) for r in receipts))
            allocated = ZERO
            for i, r in enumerate(receipts):
                if i == len(receipts) - 1:
                    part = q4(remaining - allocated)  # 末批兜差，保证合计=remaining
                else:
                    part = q4(remaining * _d(r.qty) / total_receipt)
                    allocated = q4(allocated + part)
                if part == 0:
                    continue
                post_move(
                    db,
                    material_id=mo.material_id,
                    warehouse_id=r.warehouse_id,
                    move_type="adjust",
                    qty=0,
                    unit_cost=part,  # 金额留痕：part 计入 amount
                    amount=part,
                    source_type=DOC_TYPE,
                    source_id=mo.id,
                    batch_id=r.batch_id,
                )
        else:
            # 非批次成品：物料级纯金额调整（v1 口径不变）
            unit = q4(remaining / completed)
            post_move(
                db,
                material_id=mo.material_id,
                warehouse_id=mo.warehouse_id,
                move_type="adjust",
                qty=0,
                unit_cost=unit,
                amount=remaining,
                source_type=DOC_TYPE,
                source_id=mo.id,
            )
        write_audit(db, DOC_TYPE, mo.id, "close", current_user.username,
                    role=current_user.role, doc_no=mo.doc_no,
                    field="transferred_cost", old=str(q4(transferred)),
                    new=str(q4(issued)))
        write_op_log(db, current_user.username, "manufacturing_order_close",
                     f"关闭生产工单：{mo.doc_no}，剩余未结转成本 {remaining}"
                     f"按完工批次分摊调增成品金额")
        mo.transferred_cost = q4(issued)
    elif remaining > 0 and completed == 0:
        # 零完工关闭（v3 修复成本悬空）：自动按原领料批次全额退料——
        # 材料回仓库、成本冲回，issued_qty_cost 归零，不产生任何虚假在制。
        issue_moves = db.scalars(
            select(InventoryMove).where(
                InventoryMove.source_type == DOC_TYPE,
                InventoryMove.source_id == mo.id,
                InventoryMove.move_type == MoveType.mo_issue,
                InventoryMove.qty < 0,
            )
        ).all()
        for mv in issue_moves:
            back_qty = q4(-_d(mv.qty))
            post_move(
                db,
                material_id=mv.material_id,
                warehouse_id=mv.warehouse_id,
                move_type=MoveType.mo_issue_return,
                qty=back_qty,
                unit_cost=q4(mv.unit_cost),
                source_type=DOC_TYPE,
                source_id=mo.id,
                batch_id=mv.batch_id,
            )
        # MoIssue 历史行保留作领料证据；退回证据在 mo_issue_return 流水 + 审计，
        # 成本直接清零（退回流水已按原价冲回，金额与 issued_qty_cost 一致）。
        mo.issued_qty_cost = q4(0)
        mo.transferred_cost = q4(0)
        write_audit(db, DOC_TYPE, mo.id, "close", current_user.username,
                    role=current_user.role, doc_no=mo.doc_no,
                    field="issued_qty_cost", old=str(q4(issued)), new="0")
        write_op_log(db, current_user.username, "manufacturing_order_close",
                     f"关闭生产工单：{mo.doc_no}（零完工），领料已按原批次全额退回，"
                     f"冲回成本 {remaining}",
                     doc_type=DOC_TYPE, doc_id=mo.id)
    change_status(db, mo, DOC_TYPE, DocStatus.closed, current_user.username,
                  f"关闭生产工单：{mo.doc_no}（剩余在制关闭）")
    db.commit()
    db.refresh(mo)
    return mo
