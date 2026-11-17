"""审计服务 + 审计查询接口测试。"""

from erp_lite.services.audit import write_audit, write_op_log


def test_write_audit_fields(db_session):
    write_audit(db_session, "purchase_order", 1, "update", "admin",
                field="status", old="draft", new="approved")
    db_session.commit()
    rec = db_session.query(__import__("erp_lite.models", fromlist=["AuditRecord"]).AuditRecord).one()
    assert rec.doc_type == "purchase_order"
    assert rec.doc_id == 1
    assert rec.action == "update"
    assert rec.actor == "admin"
    assert rec.field == "status"
    assert rec.old_value == "draft"
    assert rec.new_value == "approved"
    assert rec.created_at is not None


def test_write_op_log(db_session):
    write_op_log(db_session, "admin", "login", "登录成功")
    db_session.commit()
    log = db_session.query(__import__("erp_lite.models", fromlist=["OperationLog"]).OperationLog).one()
    assert log.actor == "admin"
    assert log.action == "login"
    assert log.detail == "登录成功"


def test_same_session_batch_then_single_commit(db_session):
    """审计写入只在调用方 commit 时落库（同一事务语义）。"""
    write_audit(db_session, "sales_order", 9, "create", "admin")
    write_op_log(db_session, "admin", "create_doc", "SO20261117001")
    assert db_session.query(__import__("erp_lite.models", fromlist=["AuditRecord"]).AuditRecord).count() == 0
    db_session.commit()
    assert db_session.query(__import__("erp_lite.models", fromlist=["AuditRecord"]).AuditRecord).count() == 1
    assert db_session.query(__import__("erp_lite.models", fromlist=["OperationLog"]).OperationLog).count() == 1
