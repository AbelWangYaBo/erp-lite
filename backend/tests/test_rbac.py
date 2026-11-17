"""RBAC 测试：权限表矩阵抽查、越权 403/放行 200 对照、admin 全通、/auth/me、用户管理、旧 token（无 role claim）拒绝。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from conftest import grant_supplier_cert
from erp_lite.config import settings
from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.main import app
from erp_lite.models import Material, MaterialCategory, Partner, PartnerType, User, Warehouse
from erp_lite.services.rbac import ROLE_ACTIONS, actions_for
from erp_lite.services.security import hash_password

ROLES = ["admin", "purchase", "warehouse", "production", "qa"]


# ---------- 测试基建 ----------

@pytest.fixture()
def make_role_client(db_session):
    """构造以指定角色身份访问的客户端（覆盖 get_current_user，不经过 JWT）。"""
    clients = []

    def _make(role: str, username: str = "tester") -> TestClient:
        fake = SimpleNamespace(id=1, username=username, role=role)
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: fake
        c = TestClient(app)
        clients.append(c)
        return c

    yield _make
    app.dependency_overrides.clear()


def _seed_refs(db_session):
    """PO/SO 依赖的基础数据：仓库/物料/供应商/客户。"""
    wh = Warehouse(code="W1", name="一号仓")
    mat = Material(code="M1", name="铜锭", unit="kg", category=MaterialCategory.raw)
    sup = Partner(code="SUP1", name="华东供应商", type=PartnerType.supplier)
    db_session.add_all([wh, mat, sup])
    db_session.commit()
    grant_supplier_cert(db_session, sup.id)  # V2-S4 资质拦截：供应商默认资质合规
    return wh, mat, sup


def _po_payload(db_session):
    wh, mat, sup = _seed_refs(db_session)
    return {
        "partner_id": sup.id,
        "order_date": "2026-11-17",
        "lines": [
            {"material_id": mat.id, "qty": 100, "unit_price": 10, "warehouse_id": wh.id}
        ],
    }


def _make_user(db_session, username: str, role: str, password: str = "pw123456") -> User:
    user = User(username=username, password_hash=hash_password(password), role=role)
    db_session.add(user)
    db_session.commit()
    return user


# ---------- 权限表正确性（PRD-v2 §2.5 矩阵抽查） ----------

def test_admin_has_all_actions():
    all_actions = set().union(*ROLE_ACTIONS.values())
    assert ROLE_ACTIONS["admin"] == all_actions


def test_purchase_matrix():
    acts = actions_for("purchase")
    for a in ("po.create", "po.approve", "so.create", "so.approve",
              "partner_cert.write", "audit.read"):
        assert a in acts, a
    for a in ("po.receive", "so.ship", "mo.create", "inspect.judge",
              "users.manage", "settlement.create", "master.write"):
        assert a not in acts, a


def test_warehouse_matrix():
    acts = actions_for("warehouse")
    for a in ("po.receive", "so.ship", "audit.read"):
        assert a in acts, a
    for a in ("po.create", "po.approve", "so.create", "bom.write", "mo.issue", "master.write"):
        assert a not in acts, a


def test_production_matrix():
    acts = actions_for("production")
    for a in ("mo.create", "mo.release", "mo.issue", "mo.receive",
              "bom.write", "mrp.run", "audit.read"):
        assert a in acts, a
    for a in ("po.create", "po.receive", "so.ship", "inspect.judge", "users.manage"):
        assert a not in acts, a


def test_qa_matrix():
    acts = actions_for("qa")
    for a in ("inspect.judge", "batch.dispose", "partner_cert.audit", "audit.read"):
        assert a in acts, a
    for a in ("po.create", "po.receive", "so.ship", "mo.create", "master.write", "users.manage"):
        assert a not in acts, a


def test_audit_read_and_trace_granted_to_every_role():
    for role in ROLES:
        assert "audit.read" in actions_for(role), role
        assert "batch.trace" in actions_for(role), role


def test_unknown_role_yields_no_actions():
    assert actions_for("hacker") == set()


def test_user_role_defaults_to_admin(db_session):
    user = User(username="defaults", password_hash="x")
    db_session.add(user)
    db_session.flush()
    assert user.role == "admin"


# ---------- 越权 403 ----------

def test_warehouse_create_po_403(make_role_client):
    c = make_role_client("warehouse")
    resp = c.post("/api/v1/purchase-orders", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "当前角色无权执行该操作：po.create"


def test_qa_create_po_403(make_role_client):
    c = make_role_client("qa")
    resp = c.post("/api/v1/purchase-orders", json={})
    assert resp.status_code == 403


def test_production_create_po_403(make_role_client):
    c = make_role_client("production")
    resp = c.post("/api/v1/purchase-orders", json={})
    assert resp.status_code == 403


def test_purchase_receive_po_403(db_session, make_role_client):
    po = make_role_client("purchase").post(
        "/api/v1/purchase-orders", json=_po_payload(db_session)
    ).json()
    c = make_role_client("purchase")
    resp = c.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": 100,
               "warehouse_id": po["lines"][0]["warehouse_id"]}],
    )
    assert resp.status_code == 403
    assert "po.receive" in resp.json()["detail"]


def test_production_inspect_judge_forbidden(make_role_client):
    """inspect.judge 仅 qa/admin（检验路由 S2 落地，此处锁权限表行为）。"""
    c = make_role_client("production")
    dep = require_action("inspect.judge")
    with pytest.raises(Exception) as exc:
        dep(fake := SimpleNamespace(id=1, username="tester", role="production"))
    assert getattr(exc.value, "status_code", None) == 403


def test_warehouse_create_user_403(make_role_client):
    c = make_role_client("warehouse")
    resp = c.post(
        "/api/v1/users",
        json={"username": "x", "password": "p123456", "role": "qa"},
    )
    assert resp.status_code == 403
    assert "users.manage" in resp.json()["detail"]


def test_qa_create_user_403(make_role_client):
    c = make_role_client("qa")
    resp = c.post(
        "/api/v1/users",
        json={"username": "x", "password": "p123456", "role": "admin"},
    )
    assert resp.status_code == 403


def test_purchase_master_write_403(make_role_client):
    c = make_role_client("purchase")
    resp = c.post("/api/v1/materials", json={})
    assert resp.status_code == 403
    assert "master.write" in resp.json()["detail"]


def test_qa_settlement_403(make_role_client):
    c = make_role_client("qa")
    resp = c.post("/api/v1/settlements", json={})
    assert resp.status_code == 403


def test_warehouse_so_create_403(make_role_client):
    c = make_role_client("warehouse")
    resp = c.post("/api/v1/sales-orders", json={})
    assert resp.status_code == 403


def test_qa_mrp_run_403(make_role_client):
    c = make_role_client("qa")
    resp = c.post("/api/v1/mrp/run", json={})
    assert resp.status_code == 403


def test_warehouse_bom_write_403(make_role_client):
    c = make_role_client("warehouse")
    resp = c.put("/api/v1/boms/1", json={})
    assert resp.status_code == 403


def test_production_settlement_403(make_role_client):
    c = make_role_client("production")
    resp = c.post("/api/v1/settlements", json={})
    assert resp.status_code == 403


# ---------- 放行 200 对照 + admin 全通 ----------

def test_purchase_create_po_200(db_session, make_role_client):
    c = make_role_client("purchase")
    resp = c.post("/api/v1/purchase-orders", json=_po_payload(db_session))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "draft"


def test_purchase_approve_then_warehouse_receive_200(db_session, make_role_client):
    """purchase 建+审 → warehouse 入库：矩阵关键链路放行。"""
    purchase = make_role_client("purchase")
    po = purchase.post("/api/v1/purchase-orders", json=_po_payload(db_session)).json()
    resp = purchase.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    assert resp.status_code == 200, resp.text
    wh = make_role_client("warehouse")
    resp = wh.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": 100,
               "warehouse_id": po["lines"][0]["warehouse_id"]}],
    )
    assert resp.status_code == 200, resp.text


def test_admin_full_pass(db_session, make_role_client):
    from sqlalchemy import select

    admin = make_role_client("admin")
    assert admin.post("/api/v1/purchase-orders", json=_po_payload(db_session)).status_code == 200
    assert admin.post(
        "/api/v1/users", json={"username": "u2", "password": "p123456", "role": "qa"}
    ).status_code == 200
    mat = db_session.scalar(select(Material).where(Material.code == "M1"))
    assert admin.post(
        "/api/v1/mrp/run",
        json={"demand": [{"material_id": mat.id, "qty": 10}]},
    ).status_code == 200
    assert admin.put("/api/v1/boms/1", json={"lines": []}).status_code in (200, 404)


def test_audit_read_granted_to_every_role(make_role_client):
    for role in ROLES:
        resp = make_role_client(role).get("/api/v1/audit/records")
        assert resp.status_code == 200, (role, resp.text)


def test_list_endpoints_readable_by_all_roles(db_session, make_role_client):
    for role in ROLES:
        c = make_role_client(role)
        for path in ("/api/v1/purchase-orders", "/api/v1/sales-orders",
                     "/api/v1/materials", "/api/v1/partners", "/api/v1/warehouses"):
            resp = c.get(path)
            assert resp.status_code == 200, (role, path, resp.text)


# ---------- /auth/me ----------

def test_auth_me_returns_role_and_actions(make_role_client):
    c = make_role_client("purchase", username="purchaser")
    resp = c.get("/api/v1/auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "purchaser"
    assert body["role"] == "purchase"
    assert body["actions"] == sorted(actions_for("purchase"))
    assert "po.create" in body["actions"]


def test_auth_me_requires_login(anon_client):
    resp = anon_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# ---------- 用户管理 ----------

def test_admin_create_and_list_users(make_role_client):
    admin = make_role_client("admin")
    resp = admin.post(
        "/api/v1/users", json={"username": "newqa", "password": "p123456", "role": "qa"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["username"] == "newqa"
    assert body["role"] == "qa"
    resp = admin.get("/api/v1/users")
    assert resp.status_code == 200
    assert {u["username"] for u in resp.json()["items"]} >= {"newqa"}


def test_admin_create_duplicate_user_400(make_role_client):
    admin = make_role_client("admin")
    payload = {"username": "dup", "password": "p123456", "role": "qa"}
    assert admin.post("/api/v1/users", json=payload).status_code == 200
    resp = admin.post("/api/v1/users", json=payload)
    assert resp.status_code == 400


def test_admin_update_user_role(make_role_client):
    admin = make_role_client("admin")
    uid = admin.post(
        "/api/v1/users", json={"username": "switch", "password": "p123456", "role": "qa"}
    ).json()["id"]
    resp = admin.put(f"/api/v1/users/{uid}/role", json={"role": "production"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "production"


def test_admin_update_user_role_invalid_422(make_role_client):
    admin = make_role_client("admin")
    uid = admin.post(
        "/api/v1/users", json={"username": "switch2", "password": "p123456", "role": "qa"}
    ).json()["id"]
    resp = admin.put(f"/api/v1/users/{uid}/role", json={"role": "ceo"})
    assert resp.status_code == 422


def test_update_missing_user_404(make_role_client):
    admin = make_role_client("admin")
    resp = admin.put("/api/v1/users/9999/role", json={"role": "qa"})
    assert resp.status_code == 404


def test_new_user_password_is_bcrypt_and_can_login(make_role_client, anon_client, db_session):
    admin = make_role_client("admin")
    resp = admin.post(
        "/api/v1/users", json={"username": "loginme", "password": "p123456", "role": "qa"}
    )
    assert resp.status_code == 200
    # 密码以 bcrypt 哈希落库（脱库直查验证）
    from sqlalchemy import select
    user = db_session.scalar(select(User).where(User.username == "loginme"))
    assert user.password_hash.startswith("$2")
    resp = anon_client.post(
        "/api/v1/auth/login", json={"username": "loginme", "password": "p123456"}
    )
    assert resp.status_code == 200, resp.text


# ---------- JWT role claim ----------

def test_login_token_contains_role_claim(anon_client):
    anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    token = anon_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    ).json()["token"]
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    assert payload["role"] == "admin"


def test_old_token_without_role_claim_rejected(anon_client):
    """v1 旧 token（无 role claim）：选拒绝——401 要求重新登录，不静默回退 admin。"""
    anon_client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    legacy = jwt.encode(
        {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    resp = anon_client.get(
        "/api/v1/audit/records", headers={"Authorization": f"Bearer {legacy}"}
    )
    assert resp.status_code == 401
    resp = anon_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 401


def test_new_token_full_flow_role_scoped(anon_client):
    """真实 JWT 链路：qa 登录 → 建 PO 被拒（403），audit 查询放行。"""
    _make_user_anon(anon_client)
    token = anon_client.post(
        "/api/v1/auth/login", json={"username": "qauser", "password": "qapass123"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    resp = anon_client.post("/api/v1/purchase-orders", json={}, headers=headers)
    assert resp.status_code == 403
    assert anon_client.get("/api/v1/audit/records", headers=headers).status_code == 200
    me = anon_client.get("/api/v1/auth/me", headers=headers).json()
    assert me["role"] == "qa"


def _make_user_anon(anon_client):
    """借助 anon_client 已注册的 get_db 覆盖拿到测试库 session 建用户。"""
    from erp_lite.models import User as UserModel
    from erp_lite.services.security import hash_password
    db = app.dependency_overrides[get_db]()
    db.add(UserModel(username="qauser", password_hash=hash_password("qapass123"), role="qa"))
    db.commit()
