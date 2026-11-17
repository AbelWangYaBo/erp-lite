"""往来单位 CRUD API 测试：type 过滤/唯一码/单据引用禁删/diff 留痕/鉴权。"""

from datetime import date

from erp_lite.database import get_db
from erp_lite.models import Payable, PurchaseOrder


def _create(client, code="S001", name="供应商甲", type="supplier") -> dict:
    return client.post(
        "/api/v1/partners", json={"code": code, "name": name, "type": type, "contact": "张三"}
    ).json()


def test_create_partner_success(client):
    resp = _create(client)
    assert resp["code"] == "S001"
    assert resp["type"] == "supplier"
    assert resp["id"] > 0


def test_create_duplicate_code_400(client):
    _create(client)
    resp = client.post(
        "/api/v1/partners", json={"code": "S001", "name": "重复", "type": "customer"}
    )
    assert resp.status_code == 400
    assert "编码已存在" in resp.json()["detail"]


def test_invalid_type_rejected(client):
    resp = client.post(
        "/api/v1/partners", json={"code": "S009", "name": "x", "type": "friend"}
    )
    assert resp.status_code == 422


def test_list_type_filter_and_keyword(client):
    _create(client, "S001", "供应商甲", "supplier")
    _create(client, "S002", "供应商乙", "supplier")
    _create(client, "C001", "客户丙", "customer")
    all_resp = client.get("/api/v1/partners").json()
    assert all_resp["total"] == 3
    sup = client.get("/api/v1/partners", params={"type": "supplier"}).json()
    assert sup["total"] == 2
    cus = client.get("/api/v1/partners", params={"type": "customer"}).json()
    assert cus["total"] == 1
    assert cus["items"][0]["code"] == "C001"
    kw = client.get("/api/v1/partners", params={"keyword": "甲"}).json()
    assert kw["total"] == 1


def test_update_writes_field_diff_audit(client):
    body = _create(client)
    resp = client.put(
        f"/api/v1/partners/{body['id']}",
        json={"code": "S001", "name": "供应商甲改", "type": "supplier", "contact": "李四"},
    )
    assert resp.status_code == 200
    records = client.get(
        "/api/v1/audit/records", params={"doc_type": "partner", "doc_id": body["id"]}
    ).json()
    updates = {r["field"]: r for r in records["items"] if r["action"] == "update"}
    assert set(updates) == {"name", "contact"}
    assert updates["name"]["old_value"] == "供应商甲"
    assert updates["name"]["new_value"] == "供应商甲改"
    assert updates["contact"]["old_value"] == "张三"
    assert updates["contact"]["new_value"] == "李四"


def test_delete_partner_without_references_ok(client):
    body = _create(client)
    resp = client.delete(f"/api/v1/partners/{body['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/v1/partners/{body['id']}").status_code == 404


def test_delete_partner_with_po_400(client):
    body = _create(client)
    pid = body["id"]
    db = client.app.dependency_overrides[get_db]()
    db.add(
        PurchaseOrder(
            doc_no="PO20260101001",
            status="draft",
            partner_id=pid,
            order_date=date(2026, 1, 1),
        )
    )
    db.commit()
    resp = client.delete(f"/api/v1/partners/{pid}")
    assert resp.status_code == 400
    assert "单据" in resp.json()["detail"]


def test_delete_partner_with_payable_400(client):
    body = _create(client)
    pid = body["id"]
    db = client.app.dependency_overrides[get_db]()
    db.add(
        Payable(
            partner_id=pid,
            source_type="purchase_in",
            source_id=1,
            amount=100,
            settled_amount=0,
            status="open",
        )
    )
    db.commit()
    assert client.delete(f"/api/v1/partners/{pid}").status_code == 400


def test_partners_without_token_401(anon_client):
    assert anon_client.get("/api/v1/partners").status_code == 401
