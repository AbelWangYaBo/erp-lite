"""审计写入：与业务同事务（只 add，不 commit，由调用方统一提交）。"""

from sqlalchemy.orm import Session

from erp_lite.models import AuditRecord, OperationLog

# v3 动作枚举（收敛命名，AuditRecord.action 收敛到此集合；operation_logs.action 不限）
ACTIONS = frozenset(
    {
        "create", "update", "status", "void", "close", "reject",
        "issue", "unissue", "receive", "ship", "complete",
        "inspection", "judge", "dispose", "audit", "revoke",
        "settle", "reverse", "post", "return",
    }
)


def write_audit(
    db: Session,
    doc_type: str,
    doc_id: int,
    action: str,
    actor: str,
    field: str | None = None,
    old: str | None = None,
    new: str | None = None,
    role: str | None = None,
    doc_no: str | None = None,
) -> AuditRecord:
    """单据字段级/状态级留痕（v3：带操作时角色与单据号，支撑按人/角色过滤与链路串联）。"""
    rec = AuditRecord(
        doc_type=doc_type,
        doc_id=doc_id,
        action=action,
        actor=actor,
        role=role,
        doc_no=doc_no,
        field=field,
        old_value=old,
        new_value=new,
    )
    db.add(rec)
    return rec


def write_op_log(
    db: Session,
    actor: str,
    action: str,
    detail: str | None = None,
    doc_type: str | None = None,
    doc_id: int | None = None,
) -> OperationLog:
    """全局操作日志（登录、单据动作、MRP 运算等；v3：可关联单据）。"""
    log = OperationLog(
        actor=actor,
        action=action,
        detail=detail,
        doc_type=doc_type,
        doc_id=doc_id,
    )
    db.add(log)
    return log
