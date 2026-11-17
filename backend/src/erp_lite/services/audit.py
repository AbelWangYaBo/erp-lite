"""审计写入：与业务同事务（只 add，不 commit，由调用方统一提交）。"""

from sqlalchemy.orm import Session

from erp_lite.models import AuditRecord, OperationLog


def write_audit(
    db: Session,
    doc_type: str,
    doc_id: int,
    action: str,
    actor: str,
    field: str | None = None,
    old: str | None = None,
    new: str | None = None,
) -> AuditRecord:
    """单据字段级/状态级留痕。"""
    rec = AuditRecord(
        doc_type=doc_type,
        doc_id=doc_id,
        action=action,
        actor=actor,
        field=field,
        old_value=old,
        new_value=new,
    )
    db.add(rec)
    return rec


def write_op_log(db: Session, actor: str, action: str, detail: str | None = None) -> OperationLog:
    """全局操作日志（登录、单据动作、MRP 运算等）。"""
    log = OperationLog(actor=actor, action=action, detail=detail)
    db.add(log)
    return log
