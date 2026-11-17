"""认证 + 审计路由测试。"""

from erp_lite.models import AuditRecord, OperationLog


def test_login_success_seeds_admin(anon_client):
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    assert resp.status_code == 200
    token = resp.json()["token"]
    assert isinstance(token, str) and len(token) > 20


def test_login_wrong_password_401(anon_client):
    anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "bad"}
    )
    assert resp.status_code == 401
    assert "detail" in resp.json()


def test_login_unknown_user_401(anon_client):
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "x"}
    )
    assert resp.status_code == 401


def test_protected_route_without_token_401(anon_client):
    resp = anon_client.get("/api/v1/audit/records")
    assert resp.status_code == 401


def test_real_token_grants_access(anon_client):
    token = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["token"]
    resp = anon_client.get(
        "/api/v1/audit/records", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_audit_records_pagination_and_filter(client):
    for i in range(1, 26):
        client.app.dependency_overrides  # keep override alive (no-op)
        _add_record(i)
    page1 = client.get("/api/v1/audit/records", params={"page": 1, "page_size": 20}).json()
    assert page1["total"] == 25
    assert len(page1["items"]) == 20
    page2 = client.get("/api/v1/audit/records", params={"page": 2, "page_size": 20}).json()
    assert len(page2["items"]) == 5

    single = client.get(
        "/api/v1/audit/records", params={"doc_type": "purchase_order", "doc_id": 3}
    ).json()
    assert single["total"] == 1
    assert single["items"][0]["doc_id"] == 3


def _add_record(i: int):
    from erp_lite.database import get_db
    from erp_lite.main import app

    db = app.dependency_overrides[get_db]()
    db.add(
        AuditRecord(
            doc_type="purchase_order" if i % 2 else "sales_order",
            doc_id=i,
            action="create",
            actor="tester",
        )
    )
    db.commit()


def test_audit_logs_pagination(client):
    from erp_lite.database import get_db
    from erp_lite.main import app

    db = app.dependency_overrides[get_db]()
    for i in range(1, 8):
        db.add(OperationLog(actor="tester", action="login", detail=f"第{i}次"))
    db.commit()
    resp = client.get("/api/v1/audit/logs", params={"page": 2, "page_size": 5}).json()
    assert resp["total"] == 7
    assert len(resp["items"]) == 2


def test_health_no_auth(anon_client):
    assert anon_client.get("/api/health").status_code == 200
