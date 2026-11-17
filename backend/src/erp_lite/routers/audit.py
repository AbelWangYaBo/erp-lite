"""审计查询路由：字段级留痕 + 操作日志，分页 {items, total}。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import AuditRecord, OperationLog, User

router = APIRouter(prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(get_current_user)])


class AuditRecordOut(BaseModel):
    id: int
    doc_type: str
    doc_id: int
    action: str
    actor: str
    field: str | None
    old_value: str | None
    new_value: str | None
    created_at: str

    model_config = {"from_attributes": True}


class OperationLogOut(BaseModel):
    id: int
    actor: str
    action: str
    detail: str | None
    created_at: str

    model_config = {"from_attributes": True}


class Page(BaseModel):
    items: list
    total: int


def _paginate(db: Session, stmt, page: int, page_size: int, model) -> dict:
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(model.id.desc()).limit(page_size).offset((page - 1) * page_size)
    ).scalars().all()
    items = []
    for r in rows:
        d = {
            c: (getattr(r, c).isoformat() if hasattr(getattr(r, c), "isoformat") else getattr(r, c))
            for c in ("id", "doc_type", "doc_id", "action", "actor", "field", "old_value", "new_value", "detail", "created_at")
            if hasattr(r, c)
        }
        items.append(d)
    return {"items": items, "total": total}


@router.get("/records")
def list_audit_records(
    doc_type: str | None = None,
    doc_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(AuditRecord)
    if doc_type is not None:
        stmt = stmt.where(AuditRecord.doc_type == doc_type)
    if doc_id is not None:
        stmt = stmt.where(AuditRecord.doc_id == doc_id)
    return _paginate(db, stmt, page, page_size, AuditRecord)


@router.get("/logs")
def list_operation_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(OperationLog)
    return _paginate(db, stmt, page, page_size, OperationLog)
