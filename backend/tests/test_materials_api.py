"""物料 CRUD API 测试：分页/keyword/唯一码/字段 diff 留痕/流水禁删/鉴权。"""

from erp_lite.database import get_db
from erp_lite.models import AuditRecord, InventoryMove


def _create(client, **overrides) -> dict:
    payload = {
        "code": "M001",
        "name": "铝锭",
        "spec": "A6063",
        "unit": "kg",
        "category": "raw",
    }
    payload.update(overrides)
    return client.post("/api/v1/materials", json=payload).json()


def test_create_material_success(client):
    resp = client.post(
        "/api/v1/materials",
        json={"code": "M001", "name": "铝锭", "spec": "A6063", "unit": "kg", "category": "raw"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "M001"
    assert body["category"] == "raw"
    assert body["id"] > 0


def test_create_duplicate_code_400(client):
    _create(client)
    resp = client.post(
        "/api/v1/materials",
        json={"code": "M001", "name": "重复物料", "unit": "个", "category": "semi"},
    )
    assert resp.status_code == 400
    assert "编码已存在" in resp.json()["detail"]


def test_create_invalid_category_rejected(client):
    resp = client.post(
        "/api/v1/materials",
        json={"code": "M009", "name": "x", "unit": "个", "category": "other"},
    )
    assert resp.status_code == 422


def test_list_pagination_and_keyword(client):
    codes = ["RAW-01", "RAW-02", "SEMI-01", "FIN-01"]
    for i, c in enumerate(codes):
        client.post(
            "/api/v1/materials",
            json={"code": c, "name": f"物料{i}", "unit": "个", "category": "raw"},
        )
    all_resp = client.get("/api/v1/materials").json()
    assert all_resp["total"] == 4
    page = client.get("/api/v1/materials", params={"page": 2, "page_size": 3}).json()
    assert page["total"] == 4
    assert len(page["items"]) == 1
    by_code = client.get("/api/v1/materials", params={"keyword": "RAW"}).json()
    assert by_code["total"] == 2
    by_name = client.get("/api/v1/materials", params={"keyword": "物料1"}).json()
    assert by_name["total"] == 1
    assert by_name["items"][0]["code"] == "RAW-02"


def test_get_detail_404(client):
    assert client.get("/api/v1/materials/999").status_code == 404


def test_get_detail_success(client):
    body = _create(client)
    resp = client.get(f"/api/v1/materials/{body['id']}")
    assert resp.status_code == 200
    assert resp.json()["code"] == "M001"


def test_update_writes_field_diff_audit(client):
    body = _create(client)
    mid = body["id"]
    resp = client.put(
        f"/api/v1/materials/{mid}",
        json={"code": "M001", "name": "铝锭-改", "spec": "A6063", "unit": "kg", "category": "raw"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "铝锭-改"
    records = client.get(
        "/api/v1/audit/records", params={"doc_type": "material", "doc_id": mid}
    ).json()
    updates = [r for r in records["items"] if r["action"] == "update"]
    assert len(updates) == 1
    assert updates[0]["field"] == "name"
    assert updates[0]["old_value"] == "铝锭"
    assert updates[0]["new_value"] == "铝锭-改"


def test_update_duplicate_code_400(client):
    _create(client)
    body2 = _create(client, code="M002", name="第二物料")
    resp = client.put(
        f"/api/v1/materials/{body2['id']}",
        json={"code": "M001", "name": "第二物料", "unit": "个", "category": "raw"},
    )
    assert resp.status_code == 400
    assert "编码已存在" in resp.json()["detail"]


def test_delete_material_without_moves_ok(client):
    body = _create(client)
    resp = client.delete(f"/api/v1/materials/{body['id']}")
    assert resp.status_code == 200
    assert client.get(f"/api/v1/materials/{body['id']}").status_code == 404


def test_delete_material_with_moves_400(client):
    body = _create(client)
    mid = body["id"]
    db = client.app.dependency_overrides[get_db]()
    db.add(
        InventoryMove(
            material_id=mid,
            warehouse_id=1,
            move_type="purchase_in",
            qty=10,
            unit_cost=2,
            amount=20,
            source_type="test",
            source_id=1,
        )
    )
    db.commit()
    resp = client.delete(f"/api/v1/materials/{mid}")
    assert resp.status_code == 400
    assert "流水" in resp.json()["detail"]


def test_create_writes_audit_and_op_log(client):
    body = _create(client)
    records = client.get(
        "/api/v1/audit/records", params={"doc_type": "material", "doc_id": body["id"]}
    ).json()
    assert records["total"] == 1
    assert records["items"][0]["action"] == "create"
    logs = client.get("/api/v1/audit/logs").json()
    assert any("物料" in (l["detail"] or "") for l in logs["items"])


def test_materials_without_token_401(anon_client):
    assert anon_client.get("/api/v1/materials").status_code == 401
    assert (
        anon_client.post("/api/v1/materials", json={}).status_code == 401
    )
