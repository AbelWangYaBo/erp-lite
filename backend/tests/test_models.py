"""数据模型测试：全部表可建、字段齐全、唯一约束与默认值生效。"""

from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from erp_lite.models import (
    AuditRecord,
    Bom,
    BomLine,
    DocSequence,
    DocStatus,
    InventoryMove,
    ManufacturingOrder,
    Material,
    MoIssue,
    MoveType,
    OperationLog,
    Partner,
    Payable,
    PurchaseOrder,
    PurchaseOrderLine,
    Receivable,
    SalesOrder,
    SalesOrderLine,
    Settlement,
    SettlementLine,
    User,
    Warehouse,
)


def _commit(db):
    db.commit()


def test_all_tables_exist(db_session):
    tables = set(__import__("sqlalchemy").inspect(db_session.get_bind()).get_table_names())
    expected = {
        "users",
        "materials",
        "warehouses",
        "partners",
        "purchase_orders",
        "purchase_order_lines",
        "sales_orders",
        "sales_order_lines",
        "inventory_moves",
        "boms",
        "bom_lines",
        "mrp_runs",
        "mrp_suggestions",
        "manufacturing_orders",
        "mo_issues",
        "payables",
        "receivables",
        "settlements",
        "settlement_lines",
        "audit_records",
        "operation_logs",
        "doc_sequences",
    }
    assert expected <= tables


def test_user_unique_username(db_session):
    db_session.add(User(username="u1", password_hash="x"))
    db_session.commit()
    db_session.add(User(username="u1", password_hash="y"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_master_data_unique_codes(db_session):
    db_session.add(Warehouse(code="WH01", name="原料仓"))
    db_session.add(Material(code="M001", name="钢板", spec="2mm", unit="kg", category="raw"))
    db_session.add(Partner(code="S001", name="供应商甲", type="supplier", contact="13800000000"))
    db_session.commit()
    assert db_session.query(Material).count() == 1
    db_session.add(Warehouse(code="WH01", name="重复仓库"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_doc_status_enum_and_default(db_session):
    wh = Warehouse(code="WH01", name="仓")
    mat = Material(code="M001", name="成品", unit="件", category="finished", default_warehouse=None)
    p = Partner(code="C001", name="客户甲", type="customer")
    db_session.add_all([wh, mat, p])
    db_session.commit()
    po = PurchaseOrder(doc_no="PO20261117001", partner_id=p.id, order_date=date(2026, 11, 17))
    db_session.add(po)
    db_session.commit()
    assert po.status == DocStatus.draft
    po.status = DocStatus.approved
    db_session.commit()
    assert po.status.value == "approved"
    assert isinstance(po.created_at, datetime)


def test_purchase_order_lines_cascade(db_session):
    wh = Warehouse(code="WH01", name="仓")
    mat = Material(code="M001", name="料", unit="件", category="raw")
    p = Partner(code="S001", name="供", type="supplier")
    db_session.add_all([wh, mat, p])
    db_session.commit()
    po = PurchaseOrder(doc_no="PO20261117001", partner_id=p.id, order_date=date(2026, 11, 17))
    po.lines.append(
        PurchaseOrderLine(material_id=mat.id, qty=10, unit_price=2.5, warehouse_id=wh.id)
    )
    db_session.add(po)
    db_session.commit()
    assert po.lines[0].received_qty == 0


def test_sales_and_inventory_and_production_and_finance_roundtrip(db_session):
    wh = Warehouse(code="WH01", name="仓")
    mat = Material(code="M001", name="料", unit="件", category="raw")
    p = Partner(code="C001", name="客", type="customer")
    db_session.add_all([wh, mat, p])
    db_session.commit()
    so = SalesOrder(doc_no="SO20261117001", partner_id=p.id, order_date=date(2026, 11, 17))
    so.lines.append(SalesOrderLine(material_id=mat.id, qty=5, unit_price=9.9, warehouse_id=wh.id))
    db_session.add(so)
    db_session.add(
        InventoryMove(
            material_id=mat.id,
            warehouse_id=wh.id,
            move_type=MoveType.purchase_in,
            qty=100,
            unit_cost=2,
            amount=200,
            source_type="purchase_order",
            source_id=1,
            source_line_id=1,
        )
    )
    bom = Bom(material_id=mat.id, status="active")
    bom.lines.append(BomLine(material_id=mat.id, qty_per=2, loss_pct=1))
    db_session.add(bom)
    mo = ManufacturingOrder(doc_no="MO20261117001", material_id=mat.id, planned_qty=50, warehouse_id=wh.id)
    mo.issues.append(MoIssue(material_id=mat.id, qty=10, cost_amount=20))
    db_session.add(mo)
    pay = Payable(partner_id=p.id, source_type="purchase_order", source_id=1, amount=300)
    rec = Receivable(partner_id=p.id, source_type="sales_order", source_id=1, amount=500)
    st = Settlement(type="receipt", partner_id=p.id, amount=200, date=date(2026, 11, 17))
    st.lines.append(SettlementLine(target_type="ar", target_id=1, amount=200))
    db_session.add_all([pay, rec, st])
    db_session.commit()
    assert so.lines[0].shipped_qty == 0
    assert pay.settled_amount == 0 and pay.status == "open"
    assert mo.issued_qty_cost == 0 and mo.extra_cost == 0


def test_audit_tables_and_doc_sequence(db_session):
    db_session.add(
        AuditRecord(doc_type="purchase_order", doc_id=1, action="create", actor="admin",
                    field=None, old_value=None, new_value=None)
    )
    db_session.add(OperationLog(actor="admin", action="login", detail="ok"))
    db_session.add(DocSequence(prefix="PO", seq_date="20261117", last_no=3))
    db_session.commit()
    assert db_session.query(AuditRecord).count() == 1
    assert db_session.query(OperationLog).count() == 1
    assert db_session.query(DocSequence).one().last_no == 3
