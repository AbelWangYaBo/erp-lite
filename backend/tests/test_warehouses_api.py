"""仓库 CRUD API 测试：无 DELETE 接口、唯一码、分页/keyword、diff 留痕、鉴权。"""


def _create(client, code="W001", name="原料仓") -> dict:
    return client.post("/api/v1/warehouses", json={"code": code, "name": name}).json()


def test_create_warehouse_success(client):
    resp = client.post("/api/v1/warehouses", json={"code": "W001", "name": "原料仓"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == "W001"
    assert body["id"] > 0


def test_create_duplicate_code_400(client):
    _create(client)
    resp = client.post("/api/v1/warehouses", json={"code": "W001", "name": "重复仓"})
    assert resp.status_code == 400
    assert "编码已存在" in resp.json()["detail"]


def test_list_pagination_and_keyword(client):
    for c, n in [("W001", "原料仓"), ("W002", "成品仓"), ("W003", "半成品仓")]:
        _create(client, c, n)
    all_resp = client.get("/api/v1/warehouses").json()
    assert all_resp["total"] == 3
    page = client.get("/api/v1/warehouses", params={"page": 2, "page_size": 2}).json()
    assert len(page["items"]) == 1
    kw = client.get("/api/v1/warehouses", params={"keyword": "成品"}).json()
    assert kw["total"] == 2
    kw_code = client.get("/api/v1/warehouses", params={"keyword": "W003"}).json()
    assert kw_code["total"] == 1


def test_update_writes_field_diff_audit(client):
    body = _create(client)
    resp = client.put(f"/api/v1/warehouses/{body['id']}", json={"code": "W001", "name": "原料总仓"})
    assert resp.status_code == 200
    records = client.get(
        "/api/v1/audit/records", params={"doc_type": "warehouse", "doc_id": body["id"]}
    ).json()
    updates = [r for r in records["items"] if r["action"] == "update"]
    assert len(updates) == 1
    assert updates[0]["field"] == "name"
    assert updates[0]["old_value"] == "原料仓"
    assert updates[0]["new_value"] == "原料总仓"


def test_update_404(client):
    resp = client.put("/api/v1/warehouses/999", json={"code": "W1", "name": "x"})
    assert resp.status_code == 404


def test_warehouse_no_delete_endpoint(client):
    body = _create(client)
    resp = client.delete(f"/api/v1/warehouses/{body['id']}")
    assert resp.status_code == 405


def test_warehouses_without_token_401(anon_client):
    assert anon_client.get("/api/v1/warehouses").status_code == 401
