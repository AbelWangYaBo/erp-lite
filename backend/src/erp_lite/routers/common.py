"""路由层公共设施：统一分页查询（{items, total}）与更新 diff 留痕。"""

import enum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.models import User
from erp_lite.models.base import DocStatus
from erp_lite.services.audit import write_audit, write_op_log


def paginate(db: Session, stmt, page: int, page_size: int) -> tuple[list, int]:
    """返回 (当前页行, 总数)；排序由调用方在 stmt 上先行指定。"""
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        db.execute(stmt.limit(page_size).offset((page - 1) * page_size))
        .scalars()
        .all()
    )
    return rows, total


def _plain(value) -> str | None:
    """枚举转字符串值，便于审计记录可读。"""
    if isinstance(value, enum.Enum):
        return str(value.value)
    return None if value is None else str(value)


def apply_update_with_diff_audit(
    db: Session, obj, payload, doc_type: str, actor: User
) -> int:
    """逐字段 diff 更新对象；每个变更字段写一条 update 留痕，返回变更字段数。"""
    changed = 0
    for field, new in payload.model_dump().items():
        new = _plain(new)
        old = _plain(getattr(obj, field))
        if old != new:
            write_audit(
                db,
                doc_type=doc_type,
                doc_id=obj.id,
                action="update",
                actor=actor.username,
                field=field,
                old=old,
                new=new,
            )
            setattr(obj, field, new)
            changed += 1
    return changed


def change_status(
    db: Session,
    doc,
    doc_type: str,
    new_status: DocStatus,
    actor: str,
    note: str,
) -> None:
    """单据状态机变更：写 action=status 的留痕（old/new 为状态名）+ 操作日志。"""
    old = doc.status
    doc.status = new_status
    write_audit(
        db,
        doc_type=doc_type,
        doc_id=doc.id,
        action="status",
        actor=actor,
        field="status",
        old=str(old.value),
        new=str(new_status.value),
    )
    write_op_log(db, actor, f"{doc_type}_{new_status.value}", note)

