"""销售订单 API 测试：编号独立递增/出库/库存不足/部分出库/应收生成/均价不变。"""

from decimal import Decimal

from sqlalchemy import select

from conftest import grant_supplier_cert, switch_user
from erp_lite.models import (
    InventoryMove,
    Material,
    MaterialCategory,
    Partner,
    PartnerType,
    Payable,
    Receivable,
    Warehouse,
)


def _seed(db_session):
    wh = Warehouse(code="W1", name="一号仓")
    mat = Material(code="M1", name="铜锭", unit="kg", category=MaterialCategory.raw)
    sup = Partner(code="SUP1", name="华东供应商", type=PartnerType.supplier)
    cus = Partner(code="CUS1", name="北方客户", type=PartnerType.customer)
    db_session.add_all([wh, mat, sup, cus])
    db_session.commit()
    grant_supplier_cert(db_session, sup.id)  # V2-S4 资质拦截：供应商默认资质合规
    return wh, mat, sup, cus


def _stock_up(client, db_session, mat, wh, sup, qty=100, price=10):
    """先采购入库备货，返回入库后现存量。"""
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": qty, "unit_price": price, "warehouse_id": wh.id}
            ],
        },
    )
    po = resp.json()
    switch_user(client, "approver")
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    switch_user(client, "tester")  # v3：不得自审，审核换 approver 后切回
    client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": qty, "warehouse_id": wh.id}],
    )


def _approve_so(client, so_id):
    switch_user(client, "approver")  # v3：不得自审，SO 审核换 approver
    resp = client.post(f"/api/v1/sales-orders/{so_id}/approve")
    switch_user(client, "tester")
    return resp


def _create_so(client, db_session, qty=30, price=15):
    wh, mat, sup, cus = _seed(db_session)
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": cus.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": qty, "unit_price": price, "warehouse_id": wh.id}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json(), wh, mat, sup, cus


def test_create_so_draft_with_doc_no(client, db_session):
    body, *_ = _create_so(client, db_session)
    assert body["status"] == "draft"
    assert body["doc_no"].startswith("SO20261117")
    assert body["doc_no"].endswith("001")


def test_po_so_numbering_independent(client, db_session):
    wh, mat, sup, cus = _seed(db_session)
    lines = [{"material_id": mat.id, "qty": 1, "unit_price": 1, "warehouse_id": wh.id}]
    po = client.post(
        "/api/v1/purchase-orders",
        json={"partner_id": sup.id, "order_date": "2026-11-17", "lines": lines},
    ).json()
    so = client.post(
        "/api/v1/sales-orders",
        json={"partner_id": cus.id, "order_date": "2026-11-17", "lines": lines},
    ).json()
    assert po["doc_no"].startswith("PO")
    assert so["doc_no"].startswith("SO")
    assert po["doc_no"].endswith("001")
    assert so["doc_no"].endswith("001")


def test_approve_ship_full_stock_and_receivable(client, db_session):
    body, wh, mat, sup, _ = _create_so(client, db_session, qty=30, price=15)
    _stock_up(client, db_session, mat, wh, sup, qty=100, price=10)
    resp = _approve_so(client, body["id"])
    assert resp.status_code == 200
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 30, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    assert float(resp.json()["lines"][0]["shipped_qty"]) == 30

    from erp_lite.services.inventory import get_stock

    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert qty == Decimal("70")
    assert amount == Decimal("700")  # 出库后均价仍为 10

    move = db_session.scalar(
        select(InventoryMove).where(InventoryMove.move_type == "sale_out")
    )
    assert move is not None
    assert Decimal(str(move.unit_cost)) == Decimal("10")
    assert Decimal(str(move.amount)) == Decimal("-300")

    ars = db_session.scalars(select(Receivable)).all()
    assert len(ars) == 1
    assert ars[0].amount == Decimal("450")  # 30 * 15
    assert ars[0].partner_id == body["partner_id"]


def test_ship_insufficient_stock_400(client, db_session):
    body, wh, mat, sup, _ = _create_so(client, db_session, qty=30, price=15)
    _stock_up(client, db_session, mat, wh, sup, qty=10, price=10)
    _approve_so(client, body["id"])
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 20, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400
    assert "库存不足" in resp.json()["detail"]


def test_ship_partial_then_completed(client, db_session):
    body, wh, mat, sup, _ = _create_so(client, db_session, qty=30, price=15)
    _stock_up(client, db_session, mat, wh, sup, qty=100, price=10)
    _approve_so(client, body["id"])
    line_id = body["lines"][0]["id"]
    r1 = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 10, "warehouse_id": wh.id}],
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "partial"
    r2 = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 20, "warehouse_id": wh.id}],
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "completed"


def test_ship_over_qty_400(client, db_session):
    body, wh, mat, sup, _ = _create_so(client, db_session, qty=10, price=15)
    _stock_up(client, db_session, mat, wh, sup, qty=100, price=10)
    _approve_so(client, body["id"])
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 11, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400
    assert "超发" in resp.json()["detail"]


def test_void_draft_so_no_moves(client, db_session):
    body, *_ = _create_so(client, db_session)
    resp = client.post(f"/api/v1/sales-orders/{body['id']}/void")
    assert resp.status_code == 200
    assert resp.json()["status"] == "void"
    assert db_session.scalar(select(InventoryMove.id).limit(1)) is None
    assert db_session.scalar(select(Receivable.id).limit(1)) is None


def test_ship_invalid_warehouse_400(client, db_session):
    body, *_ = _create_so(client, db_session)
    _approve_so(client, body["id"])
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/sales-orders/{body['id']}/ship",
        json=[{"line_id": line_id, "qty": 1, "warehouse_id": 9999}],
    )
    assert resp.status_code == 400


def test_so_create_requires_lines(client, db_session):
    _, _, _, cus = _seed(db_session)
    resp = client.post(
        "/api/v1/sales-orders",
        json={"partner_id": cus.id, "order_date": "2026-11-17", "lines": []},
    )
    assert resp.status_code == 422
