"""采购订单 API 测试：编号/状态机/入库（部分、全部、超收）/作废/关闭/应付生成/审计留痕。"""

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


def _create_po(client, db_session, qty=100, price=10):
    wh, mat, sup, cus = _seed(db_session)
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {
                    "material_id": mat.id,
                    "qty": qty,
                    "unit_price": price,
                    "warehouse_id": wh.id,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json(), wh, mat, sup, cus


def _approve(client, po_id):
    switch_user(client, "approver")  # v3：不得自审，审核换 approver
    resp = client.post(f"/api/v1/purchase-orders/{po_id}/approve")
    assert resp.status_code == 200, resp.text
    switch_user(client, "tester")  # 换回创建人身份
    return resp.json()


def test_create_po_draft_with_doc_no(client, db_session):
    body, *_ = _create_po(client, db_session)
    assert body["status"] == "draft"
    assert body["doc_no"].startswith("PO20261117")
    assert body["doc_no"].endswith("001")
    assert float(body["lines"][0]["qty"]) == 100
    assert float(body["lines"][0]["received_qty"]) == 0

def test_create_po_requires_lines(client, db_session):
    _, _, sup, _ = _seed(db_session)
    resp = client.post(
        "/api/v1/purchase-orders",
        json={"partner_id": sup.id, "order_date": "2026-11-17", "lines": []},
    )
    assert resp.status_code == 422


def test_get_po_detail_with_lines_and_audits(client, db_session):
    body, *_ = _create_po(client, db_session)
    resp = client.get(f"/api/v1/purchase-orders/{body['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert len(detail["lines"]) == 1
    actions = [a["action"] for a in detail["audits"]]
    assert "create" in actions


def test_update_draft_po_ok(client, db_session):
    body, wh, mat, sup, _ = _create_po(client, db_session)
    resp = client.put(
        f"/api/v1/purchase-orders/{body['id']}",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "remark": "加急",
            "lines": [
                {
                    "material_id": mat.id,
                    "qty": 200,
                    "unit_price": 10,
                    "warehouse_id": wh.id,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["lines"][0]["qty"]) == 200
    assert resp.json()["remark"] == "加急"
    records = client.get(
        "/api/v1/audit/records",
        params={"doc_type": "purchase_order", "doc_id": body["id"]},
    ).json()
    fields = {r["field"] for r in records["items"]}
    assert "remark" in fields
    assert "lines" in fields


def test_update_approved_po_400(client, db_session):
    body, *_ = _create_po(client, db_session)
    _approve(client, body["id"])
    resp = client.put(
        f"/api/v1/purchase-orders/{body['id']}",
        json={"partner_id": body["partner_id"], "order_date": "2026-11-17", "lines": body["lines"]},
    )
    assert resp.status_code == 400
    assert "草稿" in resp.json()["detail"]


def test_approve_po_and_audit(client, db_session):
    body, *_ = _create_po(client, db_session)
    result = _approve(client, body["id"])
    assert result["status"] == "approved"
    records = client.get(
        "/api/v1/audit/records",
        params={"doc_type": "purchase_order", "doc_id": body["id"]},
    ).json()
    status_rec = [r for r in records["items"] if r["action"] == "status"]
    assert status_rec and status_rec[0]["old_value"] == "draft"
    assert status_rec[0]["new_value"] == "approved"
    # 重复审核拒绝
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/approve")
    assert resp.status_code == 400


def test_receive_partial_then_completed(client, db_session):
    body, wh, mat, _, _ = _create_po(client, db_session)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]

    r1 = client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 40, "warehouse_id": wh.id}],
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "partial"
    assert float(r1.json()["lines"][0]["received_qty"]) == 40

    r2 = client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 60, "warehouse_id": wh.id}],
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "completed"

    from erp_lite.services.inventory import get_stock

    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert qty == Decimal("100")
    assert amount == Decimal("1000")


def test_receive_over_qty_400(client, db_session):
    body, wh, *_ = _create_po(client, db_session)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]
    client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 60, "warehouse_id": wh.id}],
    )
    resp = client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 50, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400
    assert "超收" in resp.json()["detail"]


def test_receive_invalid_warehouse_400(client, db_session):
    body, *_ = _create_po(client, db_session)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 10, "warehouse_id": 9999}],
    )
    assert resp.status_code == 400
    # v3：收货仓库校验改为「须与订单行计划仓库一致」（不存在仓库 id 亦触发一致性错误）
    assert "一致" in resp.json()["detail"]


def test_receive_on_draft_400(client, db_session):
    body, wh, *_ = _create_po(client, db_session)
    line_id = body["lines"][0]["id"]
    resp = client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 10, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400


def test_void_draft_po_no_stock_change(client, db_session):
    body, wh, mat, *_ = _create_po(client, db_session)
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/void")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "void"
    assert db_session.scalar(select(InventoryMove.id).limit(1)) is None
    assert db_session.scalar(select(Payable.id).limit(1)) is None


def test_void_after_receive_400(client, db_session):
    body, wh, *_ = _create_po(client, db_session)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]
    client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 10, "warehouse_id": wh.id}],
    )
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/void")
    # 部分入库后状态为 partial，不在可作废状态集内
    assert resp.status_code == 400


def test_close_po(client, db_session):
    body, wh, *_ = _create_po(client, db_session)
    _approve(client, body["id"])
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/close")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    # 已关闭不能再关闭
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/close")
    assert resp.status_code == 400


def test_close_partial_po(client, db_session):
    body, wh, *_ = _create_po(client, db_session, qty=100)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]
    client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 30, "warehouse_id": wh.id}],
    )
    resp = client.post(f"/api/v1/purchase-orders/{body['id']}/close")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


def test_payable_amount_matches_receive(client, db_session):
    body, wh, *_ = _create_po(client, db_session, qty=50, price=8)
    _approve(client, body["id"])
    line_id = body["lines"][0]["id"]
    client.post(
        f"/api/v1/purchase-orders/{body['id']}/receive",
        json=[{"line_id": line_id, "qty": 20, "warehouse_id": wh.id}],
    )
    payables = db_session.scalars(select(Payable)).all()
    assert len(payables) == 1
    assert payables[0].amount == Decimal("160")
    assert payables[0].partner_id == body["partner_id"]
