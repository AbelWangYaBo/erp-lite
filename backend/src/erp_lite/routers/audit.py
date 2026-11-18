"""审计查询路由：字段级留痕 + 操作日志（v3：带 role/doc_no，支持人/角色/时间过滤）。"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import AuditRecord, OperationLog, User

router = APIRouter(prefix="/api/v1/audit", tags=["audit"], dependencies=[Depends(get_current_user)])


class AuditRecordOut(BaseModel):
    id: int
    doc_type: str
    doc_id: int
    action: str
    actor: str
    role: str | None = None
    doc_no: str | None = None
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
    doc_type: str | None = None
    doc_id: int | None = None
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
            for c in (
                "id", "doc_type", "doc_id", "action", "actor", "role", "doc_no",
                "field", "old_value", "new_value", "detail", "doc_type", "doc_id", "created_at",
            )
            if hasattr(r, c)
        }
        items.append(d)
    return {"items": items, "total": total}


@router.get("/records", dependencies=[Depends(require_action("audit.read"))])
def list_audit_records(
    doc_type: str | None = None,
    doc_id: int | None = None,
    actor: str | None = None,
    role: str | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """审计留痕查询（v3）：可按 单据类型/单据/操作人/角色/动作/时间段 过滤。"""
    stmt = select(AuditRecord)
    if doc_type is not None:
        stmt = stmt.where(AuditRecord.doc_type == doc_type)
    if doc_id is not None:
        stmt = stmt.where(AuditRecord.doc_id == doc_id)
    if actor is not None:
        stmt = stmt.where(AuditRecord.actor == actor)
    if role is not None:
        stmt = stmt.where(AuditRecord.role == role)
    if action is not None:
        stmt = stmt.where(AuditRecord.action == action)
    if from_ is not None:
        stmt = stmt.where(AuditRecord.created_at >= from_)
    if to is not None:
        stmt = stmt.where(AuditRecord.created_at <= to)
    return _paginate(db, stmt, page, page_size, AuditRecord)


@router.get("/logs", dependencies=[Depends(require_action("audit.read"))])
def list_operation_logs(
    doc_type: str | None = None,
    doc_id: int | None = None,
    actor: str | None = None,
    action: str | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """操作日志查询（v3）：补 单据关联/操作人/动作/时间 过滤。"""
    stmt = select(OperationLog)
    if doc_type is not None:
        stmt = stmt.where(OperationLog.doc_type == doc_type)
    if doc_id is not None:
        stmt = stmt.where(OperationLog.doc_id == doc_id)
    if actor is not None:
        stmt = stmt.where(OperationLog.actor == actor)
    if action is not None:
        stmt = stmt.where(OperationLog.action == action)
    if from_ is not None:
        stmt = stmt.where(OperationLog.created_at >= from_)
    if to is not None:
        stmt = stmt.where(OperationLog.created_at <= to)
    return _paginate(db, stmt, page, page_size, OperationLog)
