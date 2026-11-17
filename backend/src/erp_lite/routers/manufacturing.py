"""生产工单 MO：建单/下达(approve)/领料(issue)/完工入库(receive)/关闭(close)。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import (
    DocStatus,
    ManufacturingOrder,
    Material,
    MoIssue,
    User,
    Warehouse,
)
from erp_lite.routers.common import change_status, paginate
from erp_lite.schemas.production import (
    MoCreate,
    MoDetail,
    MoIssueIn,
    MoOut,
    MoReceiveIn,
)
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.inventory import _d, get_stock, post_move, q4
from erp_lite.services.mrp import expand_requirements, mo_completed_qty
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


@router.post("", response_model=MoOut)
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
    write_audit(db, DOC_TYPE, mo.id, "create", current_user.username)
    write_op_log(db, current_user.username, "manufacturing_order_create",
                 f"新增生产工单：{mo.doc_no}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/approve", response_model=MoOut)
def approve_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.draft,), "审核（下达）")
    change_status(db, mo, DOC_TYPE, DocStatus.approved, current_user.username,
                  f"下达生产工单：{mo.doc_no}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/issue", response_model=MoDetail)
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
        # 指定行：物料有 BOM 则按 BOM 递归展开，无 BOM 直接采用
        requirements: dict[int, Decimal] = {}
        for line in body.lines:
            for mid, qty in expand_requirements(
                db, line.material_id, _d(line.qty)
            ).items():
                requirements[mid] = q4(requirements.get(mid, ZERO) + qty)
    else:
        # 按 BOM × 本次完工比例展开；qty 缺省=整单计划量
        ratio_qty = _d(body.qty) if body.qty is not None else planned
        try:
            requirements = expand_requirements(db, mo.material_id, ratio_qty)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            )
        if mo.material_id in requirements:
            # 展开结果含成品自身（无 BOM 成品）时报错：领料对象不能是成品本身
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该物料无 BOM，请通过 lines 指定领料明细",
            )
    if not requirements:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="领料明细不能为空"
        )

    total_cost = ZERO
    for mid, qty in requirements.items():
        stock_qty, _ = get_stock(db, mid, mo.warehouse_id)
        if stock_qty < qty:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"库存不足：物料 {mid} 现存量 {stock_qty}，需领料 {qty}",
            )
    for mid, qty in requirements.items():
        move = post_move(
            db,
            material_id=mid,
            warehouse_id=mo.warehouse_id,
            move_type="mo_issue",
            qty=-qty,
            source_type=DOC_TYPE,
            source_id=mo.id,
        )
        cost = q4(-move.amount)  # 出库金额为负，成本取绝对值
        db.add(MoIssue(mo_id=mo.id, material_id=mid, qty=qty, cost_amount=cost))
        total_cost = q4(total_cost + cost)
    mo.issued_qty_cost = q4(_d(mo.issued_qty_cost) + total_cost)
    write_audit(db, DOC_TYPE, mo.id, "issue", current_user.username,
                field="issued_qty_cost",
                old=str(_d(mo.issued_qty_cost) - total_cost),
                new=str(_d(mo.issued_qty_cost)))
    write_op_log(db, current_user.username, "manufacturing_order_issue",
                 f"生产领料：{mo.doc_no}，本次领料成本 {total_cost}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/receive", response_model=MoOut)
def receive_mo(
    mo_id: int,
    body: MoReceiveIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial), "完工入库")
    planned = _d(mo.planned_qty)
    completed = mo_completed_qty(db, mo.id)
    qty = _d(body.qty)
    if completed + qty > planned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"累计完工将超过计划量：已完工 {completed}，计划 {planned}，本次 {qty}",
        )
    ratio = qty / planned
    extra_cost = _d(body.extra_cost)
    # v1 简化规则：入库单价 =（累计领料成本 × 本次完工比例 + 本次制费）/ 本次入库数量
    transferred = q4(_d(mo.issued_qty_cost) * ratio)
    unit_cost = q4((transferred + extra_cost) / qty)
    post_move(
        db,
        material_id=mo.material_id,
        warehouse_id=mo.warehouse_id,
        move_type="mo_receipt",
        qty=qty,
        unit_cost=unit_cost,
        source_type=DOC_TYPE,
        source_id=mo.id,
    )
    mo.extra_cost = q4(_d(mo.extra_cost) + extra_cost)
    new_status = DocStatus.completed if completed + qty >= planned else DocStatus.partial
    if new_status != mo.status:
        change_status(db, mo, DOC_TYPE, new_status, current_user.username,
                      f"完工入库：{mo.doc_no} 本次 {qty}，单价 {unit_cost}")
    else:
        write_audit(db, DOC_TYPE, mo.id, "receive", current_user.username,
                    field="completed_qty", old=str(completed), new=str(completed + qty))
        write_op_log(db, current_user.username, "manufacturing_order_receive",
                     f"完工入库：{mo.doc_no} 本次 {qty}，单价 {unit_cost}")
    db.commit()
    db.refresh(mo)
    return mo


@router.post("/{mo_id}/close", response_model=MoOut)
def close_mo(
    mo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManufacturingOrder:
    mo = _get_or_404(db, mo_id)
    _require_status(mo, (DocStatus.approved, DocStatus.partial, DocStatus.completed),
                    "关闭")
    change_status(db, mo, DOC_TYPE, DocStatus.closed, current_user.username,
                  f"关闭生产工单：{mo.doc_no}（剩余在制关闭）")
    db.commit()
    db.refresh(mo)
    return mo
