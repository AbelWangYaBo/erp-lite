"""销售订单：草稿→审核→出库（部分/全部）→关闭/作废 状态机 + 应收生成。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import (
    AuditRecord,
    Batch,
    DocStatus,
    Material,
    SalesOrder,
    SalesOrderLine,
    User,
    Warehouse,
)
from erp_lite.models.finance import Receivable
from erp_lite.routers.common import change_status, paginate
from erp_lite.schemas.orders import (
    AuditOut,
    ReceiveLineIn,
    SalesOrderCreate,
    SalesOrderDetail,
    SalesOrderOut,
    SalesOrderUpdate,
)
from erp_lite.schemas.orders import OrderLineCreate as _Line
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.batches import ensure_batch_usable
from erp_lite.services.inventory import _d, q4, post_move
from erp_lite.services.numbering import next_doc_no
from erp_lite.services.orders import (
    create_order,
    update_draft_order,
    validate_order_refs,
)

router = APIRouter(
    prefix="/api/v1/sales-orders",
    tags=["sales"],
    dependencies=[Depends(get_current_user)],
)

DOC_TYPE = "sales_order"


def _get_or_404(db: Session, so_id: int) -> SalesOrder:
    obj = db.get(SalesOrder, so_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="销售订单不存在")
    return obj


def _get_or_404_for_update(db: Session, so_id: int) -> SalesOrder:
    """出库用：FOR UPDATE 锁 SO 行后再读。

    PostgreSQL 下该行锁串行化同一 SO 的并发出库，防止并发事务同时通过
    「剩余可发/库存充足」校验导致超发；SQLite 忽略 FOR UPDATE（测试单连接串行）。
    """
    obj = db.scalar(select(SalesOrder).where(SalesOrder.id == so_id).with_for_update())
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="销售订单不存在")
    return obj


@router.get("")
def list_sales_orders(
    keyword: str | None = None,
    status_filter: DocStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(SalesOrder).order_by(SalesOrder.id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(SalesOrder.doc_no.ilike(like), SalesOrder.remark.ilike(like)))
    if status_filter is not None:
        stmt = stmt.where(SalesOrder.status == status_filter)
    items, total = paginate(db, stmt, page, page_size)
    return {"items": [SalesOrderOut.model_validate(o).model_dump(mode="json") for o in items], "total": total}


@router.get("/{so_id}", response_model=SalesOrderDetail)
def get_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrderDetail:
    so = _get_or_404(db, so_id)
    audits = db.scalars(
        select(AuditRecord)
        .where(AuditRecord.doc_type == DOC_TYPE, AuditRecord.doc_id == so_id)
        .order_by(AuditRecord.id.desc())
        .limit(20)
    ).all()
    detail = SalesOrderDetail.model_validate(so)
    detail.audits = [AuditOut.model_validate(a) for a in audits]
    return detail


@router.post("", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.create"))])
def create_sales_order(
    payload: SalesOrderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    validate_order_refs(db, payload.partner_id, payload.lines)
    doc_no = next_doc_no(db, "SO", payload.order_date)
    so = create_order(
        db,
        order_model=SalesOrder,
        line_model=SalesOrderLine,
        payload=payload,
        created_by=current_user.username,
    )
    so.doc_no = doc_no
    db.flush()  # doc_no 先赋值再落库（doc_no 非空）
    write_audit(db, DOC_TYPE, so.id, "create", current_user.username)
    write_op_log(db, current_user.username, "sales_order_create", f"新增销售订单：{so.doc_no}")
    db.commit()
    db.refresh(so)
    return so


@router.put("/{so_id}", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.update"))])
def update_sales_order(
    so_id: int,
    payload: SalesOrderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    so = _get_or_404(db, so_id)
    if so.status != DocStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿状态的销售订单可修改"
        )
    if payload.partner_id is not None or payload.lines is not None:
        validate_order_refs(
            db,
            payload.partner_id or so.partner_id,
            payload.lines
            or [
                _Line(
                    material_id=l.material_id,
                    qty=l.qty,
                    unit_price=l.unit_price,
                    warehouse_id=l.warehouse_id,
                )
                for l in so.lines
            ],
        )
    update_draft_order(db, so, payload, SalesOrderLine, DOC_TYPE, current_user)
    write_op_log(db, current_user.username, "sales_order_update", f"修改销售订单：{so.doc_no}")
    db.commit()
    db.refresh(so)
    return so


@router.post("/{so_id}/approve", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.approve"))])
def approve_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    so = _get_or_404(db, so_id)
    if so.status != DocStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿状态的销售订单可审核"
        )
    change_status(
        db, so, DOC_TYPE, DocStatus.approved, current_user.username,
        f"审核销售订单：{so.doc_no}",
    )
    db.commit()
    db.refresh(so)
    return so


@router.post("/{so_id}/ship", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.ship"))])
def ship_sales_order(
    so_id: int,
    body: list[ReceiveLineIn],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    so = _get_or_404_for_update(db, so_id)
    if so.status not in (DocStatus.approved, DocStatus.partial):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅已审核/部分出库状态可出库"
        )
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="出库明细不能为空")
    line_map = {l.id: l for l in so.lines}

    # 逐行校验：行归属、仓库、超发、批次（批次物料必带）、库存充足
    # （同一物料/批次/仓库的本次合计需求一起校验；非批次物料保持 v1 物料级口径）
    demand: dict[tuple[int, int, int | None], Decimal] = {}
    for item in body:
        line = line_map.get(item.line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"出库行不属于该订单：{item.line_id}",
            )
        if db.get(Warehouse, item.warehouse_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"仓库不存在：{item.warehouse_id}",
            )
        material = db.get(Material, line.material_id)
        if material.batch_required:
            if item.batch_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"物料 {material.code} 已启用批次管理，出库必须指定批次",
                )
            batch = db.get(Batch, item.batch_id)
            if batch is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"批次不存在：{item.batch_id}",
                )
            if batch.material_id != line.material_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"批次 {batch.batch_no} 不属于物料 {material.code}，禁止出库",
                )
            # v2-S3 放行拦截：仅 qualified 批次可出库（pending/rejected/closed 400）
            try:
                ensure_batch_usable(batch)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                )
        remaining = _d(line.qty) - _d(line.shipped_qty)
        if item.qty > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"超发出库：行剩余可发 {q4(remaining)}，本次申请 {item.qty}",
            )
        key = (line.material_id, item.warehouse_id, item.batch_id)
        demand[key] = demand.get(key, Decimal("0")) + item.qty
    from erp_lite.services.inventory import get_stock

    for (material_id, warehouse_id, batch_id), need in demand.items():
        stock_qty, _ = get_stock(db, material_id, warehouse_id, batch_id=batch_id)
        scope = f"批次 {batch_id}" if batch_id is not None else f"物料 {material_id}"
        if stock_qty < need:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"库存不足：现存量 {stock_qty}，需出库 {need}（{scope} / 仓库 {warehouse_id}）",
            )

    for item in body:
        line = line_map[item.line_id]
        post_move(
            db,
            material_id=line.material_id,
            warehouse_id=item.warehouse_id,
            move_type="sale_out",
            qty=-item.qty,  # 出库为负
            unit_cost=0,  # 出库成本由 post_move 按当前移动加权均价确定
            source_type=DOC_TYPE,
            source_id=so.id,
            source_line_id=line.id,
            batch_id=item.batch_id,
        )
        line.shipped_qty = q4(_d(line.shipped_qty) + item.qty)

    # 应收按本次出库金额生成
    ship_amount = Decimal("0")
    for item in body:
        line = line_map[item.line_id]
        ship_amount += q4(item.qty * _d(line.unit_price))

    new_status = (
        DocStatus.completed
        if all(_d(l.shipped_qty) >= _d(l.qty) for l in so.lines)
        else DocStatus.partial
    )
    if new_status != so.status:
        change_status(
            db, so, DOC_TYPE, new_status, current_user.username,
            f"销售出库：{so.doc_no} 本次出库金额 {ship_amount}",
        )
    db.add(
        Receivable(
            partner_id=so.partner_id,
            source_type=DOC_TYPE,
            source_id=so.id,
            amount=ship_amount,
        )
    )
    write_op_log(
        db, current_user.username, "sales_order_ship",
        f"销售出库：{so.doc_no} 本次出库金额 {ship_amount}",
    )
    db.commit()
    db.refresh(so)
    return so


@router.post("/{so_id}/void", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.void"))])
def void_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    so = _get_or_404(db, so_id)
    if so.status not in (DocStatus.draft, DocStatus.approved):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿/已审核状态可作废"
        )
    if any(_d(l.shipped_qty) > 0 for l in so.lines):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="已有出库记录，禁止作废"
        )
    change_status(
        db, so, DOC_TYPE, DocStatus.void, current_user.username,
        f"作废销售订单：{so.doc_no}",
    )
    db.commit()
    db.refresh(so)
    return so


@router.post("/{so_id}/close", response_model=SalesOrderOut, dependencies=[Depends(require_action("so.close"))])
def close_sales_order(
    so_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SalesOrder:
    so = _get_or_404(db, so_id)
    if so.status not in (DocStatus.approved, DocStatus.partial):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="仅已审核/部分出库状态可关闭"
        )
    change_status(
        db, so, DOC_TYPE, DocStatus.closed, current_user.username,
        f"关闭销售订单：{so.doc_no}（剩余未发量关闭）",
    )
    db.commit()
    db.refresh(so)
    return so
