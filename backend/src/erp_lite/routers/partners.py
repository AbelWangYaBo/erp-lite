"""往来单位 CRUD：type 过滤、唯一码校验、单据引用禁删、审计留痕。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import (
    Payable,
    Partner,
    PartnerType,
    PurchaseOrder,
    Receivable,
    SalesOrder,
    User,
)
from erp_lite.routers.common import apply_update_with_diff_audit, paginate
from erp_lite.schemas import (
    PartnerCreate,
    PartnerOut,
    PartnerPage,
    PartnerUpdate,
)
from erp_lite.services.audit import write_audit, write_op_log

router = APIRouter(
    prefix="/api/v1/partners", tags=["partners"], dependencies=[Depends(get_current_user)]
)

DOC_TYPE = "partner"


def _get_or_404(db: Session, partner_id: int) -> Partner:
    obj = db.get(Partner, partner_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="往来单位不存在")
    return obj


def _ensure_code_free(db: Session, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Partner).where(Partner.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Partner.id != exclude_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="往来单位编码已存在")


def _ensure_no_documents(db: Session, partner_id: int) -> None:
    referenced = any(
        db.scalar(stmt.limit(1)) is not None
        for stmt in (
            select(PurchaseOrder.id).where(PurchaseOrder.partner_id == partner_id),
            select(SalesOrder.id).where(SalesOrder.partner_id == partner_id),
            select(Payable.id).where(Payable.partner_id == partner_id),
            select(Receivable.id).where(Receivable.partner_id == partner_id),
        )
    )
    if referenced:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该往来单位已被单据引用，禁止删除",
        )


@router.get("", response_model=PartnerPage)
def list_partners(
    keyword: str | None = None,
    type: PartnerType | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Partner).order_by(Partner.id)
    if type is not None:
        stmt = stmt.where(Partner.type == type)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Partner.code.ilike(like), Partner.name.ilike(like)))
    items, total = paginate(db, stmt, page, page_size)
    return {"items": items, "total": total}


@router.get("/{partner_id}", response_model=PartnerOut)
def get_partner(
    partner_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Partner:
    return _get_or_404(db, partner_id)


@router.post("", response_model=PartnerOut, dependencies=[Depends(require_action("master.write"))])
def create_partner(
    payload: PartnerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Partner:
    _ensure_code_free(db, payload.code)
    obj = Partner(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(db, DOC_TYPE, obj.id, "create", current_user.username)
    write_op_log(db, current_user.username, "partner_create", f"新增往来单位：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{partner_id}", response_model=PartnerOut, dependencies=[Depends(require_action("master.write"))])
def update_partner(
    partner_id: int,
    payload: PartnerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Partner:
    obj = _get_or_404(db, partner_id)
    if payload.code != obj.code:
        _ensure_code_free(db, payload.code, exclude_id=obj.id)
    apply_update_with_diff_audit(db, obj, payload, DOC_TYPE, current_user)
    write_op_log(db, current_user.username, "partner_update", f"修改往来单位：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{partner_id}", dependencies=[Depends(require_action("master.write"))])
def delete_partner(
    partner_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    obj = _get_or_404(db, partner_id)
    _ensure_no_documents(db, partner_id)
    write_audit(db, DOC_TYPE, obj.id, "delete", current_user.username)
    write_op_log(db, current_user.username, "partner_delete", f"删除往来单位：{obj.code}")
    db.delete(obj)
    db.commit()
    return {"ok": True}
