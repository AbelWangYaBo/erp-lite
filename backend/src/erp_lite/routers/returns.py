"""退货单（v3）：采购退货列表（处置联动生成，只读）+ 销售退货创建/列表。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import Batch, SalesOrder, SalesReturn, User
from erp_lite.models.purchase import PurchaseReturn
from erp_lite.routers.common import paginate
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.returns import create_sales_return

purchase_returns_router = APIRouter(
    prefix="/api/v1/purchase-returns",
    tags=["returns"],
    dependencies=[Depends(get_current_user)],
)

sales_returns_router = APIRouter(
    prefix="/api/v1/sales-returns",
    tags=["returns"],
    dependencies=[Depends(get_current_user)],
)


class _PurchaseReturnOut(BaseModel):
    id: int
    doc_no: str
    po_id: int
    batch_id: int
    qty: Decimal
    amount: Decimal
    reason: str | None
    created_by: str | None
    created_at: object

    model_config = {"from_attributes": True}


class SalesReturnCreate(BaseModel):
    so_id: int
    batch_id: int
    qty: Decimal = Field(gt=0)
    reason: str | None = Field(default=None, max_length=255)


class SalesReturnOut(BaseModel):
    """销售退货出参（独立定义：不带采购侧 po_id 必填字段）。"""

    id: int
    doc_no: str
    so_id: int
    batch_id: int
    qty: Decimal
    amount: Decimal
    reason: str | None
    created_by: str | None
    created_at: object

    model_config = {"from_attributes": True}


@purchase_returns_router.get("")
def list_purchase_returns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(PurchaseReturn).order_by(PurchaseReturn.id.desc())
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [
            _PurchaseReturnOut.model_validate(r).model_dump(mode="json") for r in items
        ],
        "total": total,
    }


@sales_returns_router.get("", dependencies=[Depends(require_action("ar.read"))])
def list_sales_returns(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(SalesReturn).order_by(SalesReturn.id.desc())
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [
            SalesReturnOut.model_validate(r).model_dump(mode="json") for r in items
        ],
        "total": total,
    }


@sales_returns_router.post(
    "",
    dependencies=[Depends(require_action("sales_return.create"))],
)
def create_sales_return_api(
    body: SalesReturnCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """销售退货：回流入库（批次回 pending 重检）+ 冲应收 + 冲已发量。"""
    so = db.get(SalesOrder, body.so_id)
    if so is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="销售订单不存在")
    batch = db.get(Batch, body.batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    try:
        ret = create_sales_return(
            db,
            batch=batch,
            so=so,
            qty=body.qty,
            reason=body.reason,
            actor=current_user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    write_audit(
        db, "sales_return", ret.id, "create", current_user.username,
        role=current_user.role, doc_no=ret.doc_no,
        field="qty/amount", new=f"{ret.qty}/{ret.amount}",
    )
    write_op_log(
        db, current_user.username, "sales_return_create",
        f"销售退货：{ret.doc_no}（SO {so.doc_no}，批次 {batch.batch_no} × {ret.qty}），"
        f"批次已回待检，应收已冲减 {ret.amount}",
        doc_type="sales_return", doc_id=ret.id,
    )
    db.commit()
    db.refresh(ret)
    return SalesReturnOut.model_validate(ret).model_dump(mode="json")
