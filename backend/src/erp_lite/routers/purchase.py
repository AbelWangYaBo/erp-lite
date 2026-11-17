"""采购订单：草稿→审核→入库（部分/全部）→关闭/作废 状态机 + 应付生成。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import (
    AuditRecord,
    DocStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
    Warehouse,
)
from erp_lite.models.finance import Payable
from erp_lite.routers.common import change_status, paginate
from erp_lite.schemas.orders import (
    AuditOut,
    PurchaseOrderCreate,
    PurchaseOrderDetail,
    PurchaseOrderOut,
    PurchaseOrderUpdate,
    ReceiveLineIn,
)
from erp_lite.schemas.orders import OrderLineCreate as _Line
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.inventory import _d, q4, post_move
from erp_lite.services.numbering import next_doc_no
from erp_lite.services.orders import (
    create_order,
    update_draft_order,
    validate_order_refs,
)

router = APIRouter(
    prefix="/api/v1/purchase-orders",
    tags=["purchase"],
    dependencies=[Depends(get_current_user)],
)

DOC_TYPE = "purchase_order"


def _get_or_404(db: Session, po_id: int) -> PurchaseOrder:
    obj = db.get(PurchaseOrder, po_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采购订单不存在")
    return obj


def _get_or_404_for_update(db: Session, po_id: int) -> PurchaseOrder:
    """入库用：FOR UPDATE 锁 PO 行后再读。

    PostgreSQL 下该行锁串行化同一 PO 的并发入库，防止并发事务同时通过
    「剩余可收」校验导致超收；SQLite 忽略 FOR UPDATE（测试内存库单连接串行）。
    """
    obj = db.scalar(
        select(PurchaseOrder).where(PurchaseOrder.id == po_id).with_for_update()
    )
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="采购订单不存在")
    return obj


@router.get("", response_model=None)
def list_purchase_orders(
    keyword: str | None = None,
    status_filter: DocStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(PurchaseOrder.doc_no.ilike(like), PurchaseOrder.remark.ilike(like)))
    if status_filter is not None:
        stmt = stmt.where(PurchaseOrder.status == status_filter)
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [
            PurchaseOrderOut.model_validate(o).model_dump(mode="json") for o in items
        ],
        "total": total,
    }


@router.get("/{po_id}", response_model=PurchaseOrderDetail)
def get_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrderDetail:
    po = _get_or_404(db, po_id)
    audits = db.scalars(
        select(AuditRecord)
        .where(AuditRecord.doc_type == DOC_TYPE, AuditRecord.doc_id == po_id)
        .order_by(AuditRecord.id.desc())
        .limit(20)
    ).all()
    detail = PurchaseOrderDetail.model_validate(po)
    detail.audits = [AuditOut.model_validate(a) for a in audits]
    return detail


@router.post("", response_model=PurchaseOrderOut)
def create_purchase_order(
    payload: PurchaseOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    validate_order_refs(db, payload.partner_id, payload.lines)
    doc_no = next_doc_no(db, "PO", payload.order_date)
    po = create_order(
        db,
        order_model=PurchaseOrder,
        line_model=PurchaseOrderLine,
        payload=payload,
        created_by=current_user.username,
    )
    po.doc_no = doc_no
    db.flush()  # doc_no 先赋值再落库（doc_no 非空）
    write_audit(db, DOC_TYPE, po.id, "create", current_user.username)
    write_op_log(db, current_user.username, "purchase_order_create", f"新增采购订单：{po.doc_no}")
    db.commit()
    db.refresh(po)
    return po


@router.put("/{po_id}", response_model=PurchaseOrderOut)
def update_purchase_order(
    po_id: int,
    payload: PurchaseOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    po = _get_or_404(db, po_id)
    if po.status != DocStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿状态的采购订单可修改"
        )
    if payload.partner_id is not None or payload.lines is not None:
        validate_order_refs(
            db,
            payload.partner_id or po.partner_id,
            payload.lines or [
                _Line(
                    material_id=l.material_id,
                    qty=l.qty,
                    unit_price=l.unit_price,
                    warehouse_id=l.warehouse_id,
                )
                for l in po.lines
            ],
        )
    changed = update_draft_order(
        db, po, payload, PurchaseOrderLine, DOC_TYPE, current_user
    )
    write_op_log(
        db, current_user.username, "purchase_order_update", f"修改采购订单：{po.doc_no}"
    )
    db.commit()
    db.refresh(po)
    return po


@router.post("/{po_id}/approve", response_model=PurchaseOrderOut)
def approve_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    po = _get_or_404(db, po_id)
    if po.status != DocStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿状态的采购订单可审核"
        )
    change_status(
        db, po, DOC_TYPE, DocStatus.approved, current_user.username,
        f"审核采购订单：{po.doc_no}",
    )
    db.commit()
    db.refresh(po)
    return po


@router.post("/{po_id}/receive", response_model=PurchaseOrderOut)
def receive_purchase_order(
    po_id: int,
    body: list[ReceiveLineIn],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    po = _get_or_404_for_update(db, po_id)
    if po.status not in (DocStatus.approved, DocStatus.partial):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅已审核/部分入库状态可入库"
        )
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="入库明细不能为空")
    line_map = {l.id: l for l in po.lines}
    total_amount = Decimal("0")
    for item in body:
        line = line_map.get(item.line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"入库行不属于该订单：{item.line_id}",
            )
        if db.get(Warehouse, item.warehouse_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"仓库不存在：{item.warehouse_id}",
            )
        remaining = _d(line.qty) - _d(line.received_qty)
        if item.qty > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"超收入库：行剩余可收 {q4(remaining)}，本次申请 {item.qty}",
            )
        post_move(
            db,
            material_id=line.material_id,
            warehouse_id=item.warehouse_id,
            move_type="purchase_in",
            qty=item.qty,
            unit_cost=line.unit_price,  # 入库单价 = PO 行单价
            source_type=DOC_TYPE,
            source_id=po.id,
            source_line_id=line.id,
        )
        line.received_qty = q4(_d(line.received_qty) + item.qty)
        total_amount += q4(item.qty * _d(line.unit_price))

    new_status = (
        DocStatus.completed
        if all(_d(l.received_qty) >= _d(l.qty) for l in po.lines)
        else DocStatus.partial
    )
    if new_status != po.status:
        change_status(
            db, po, DOC_TYPE, new_status, current_user.username,
            f"采购入库：{po.doc_no} 本次入库金额 {total_amount}",
        )
    # 生成应付：金额 = 本次入库金额（S5 做核销与对账）
    db.add(
        Payable(
            partner_id=po.partner_id,
            source_type=DOC_TYPE,
            source_id=po.id,
            amount=total_amount,
        )
    )
    write_op_log(
        db, current_user.username, "purchase_order_receive",
        f"采购入库：{po.doc_no} 本次入库金额 {total_amount}",
    )
    db.commit()
    db.refresh(po)
    return po


@router.post("/{po_id}/void", response_model=PurchaseOrderOut)
def void_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    po = _get_or_404(db, po_id)
    if po.status not in (DocStatus.draft, DocStatus.approved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿/已审核状态可作废"
        )
    if any(_d(l.received_qty) > 0 for l in po.lines):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="已有入库记录，禁止作废"
        )
    change_status(
        db, po, DOC_TYPE, DocStatus.void, current_user.username,
        f"作废采购订单：{po.doc_no}",
    )
    db.commit()
    db.refresh(po)
    return po


@router.post("/{po_id}/close", response_model=PurchaseOrderOut)
def close_purchase_order(
    po_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PurchaseOrder:
    po = _get_or_404(db, po_id)
    if po.status not in (DocStatus.approved, DocStatus.partial):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅已审核/部分入库状态可关闭"
        )
    change_status(
        db, po, DOC_TYPE, DocStatus.closed, current_user.username,
        f"关闭采购订单：{po.doc_no}（剩余未收量关闭）",
    )
    db.commit()
    db.refresh(po)
    return po
