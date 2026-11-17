"""供应商资质（V2-S4）：CRUD + 审核 + 权限矩阵 + PO 有效期拦截 + 临期预警列表。"""

from datetime import date, datetime, timedelta

from pytest import fixture
from fastapi.testclient import TestClient
from sqlalchemy import select
from types import SimpleNamespace

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.main import app
from erp_lite.models import (
    Material,
    MaterialCategory,
    OperationLog,
    Partner,
    PartnerCert,
    PartnerType,
    Warehouse,
)


# ---------- 测试基建 ----------

@fixture()
def make_role_client(db_session):
    """以指定角色身份访问的客户端（覆盖 get_current_user，不经过 JWT）。"""
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


@fixture()
def supplier(db_session) -> Partner:
    obj = Partner(code="SUP1", name="测试供应商", type=PartnerType.supplier)
    db_session.add(obj)
    db_session.commit()
    return obj


@fixture()
def customer(db_session) -> Partner:
    obj = Partner(code="CUS1", name="测试客户", type=PartnerType.customer)
    db_session.add(obj)
    db_session.commit()
    return obj


@fixture()
def po_refs(db_session, supplier):
    """PO 下单依赖：仓库 + 物料 + 供应商。"""
    wh = Warehouse(code="W1", name="一号仓")
    mat = Material(code="M1", name="铜锭", unit="kg", category=MaterialCategory.raw)
    db_session.add_all([wh, mat])
    db_session.commit()
    return {
        "partner_id": supplier.id,
        "order_date": date.today().isoformat(),
        "lines": [
            {"material_id": mat.id, "qty": 10, "unit_price": 5, "warehouse_id": wh.id}
        ],
    }


def make_cert_payload(partner_id: int, valid_until: date | None = None) -> dict:
    return {
        "partner_id": partner_id,
        "cert_name": "医疗器械生产许可证",
        "cert_no": "CERT-2026-001",
        "issuer": "省药品监督管理局",
        "valid_from": (date.today() - timedelta(days=365)).isoformat(),
        "valid_until": (valid_until or date.today() + timedelta(days=365)).isoformat(),
        "file_ref": "/data/certs/CERT-2026-001.pdf",
    }


def create_cert(db_session, partner_id: int, *, valid_until: date, audited: bool = True) -> PartnerCert:
    """测试直造资质（绕过 API，用于构造拦截/临期场景）。"""
    cert = PartnerCert(
        partner_id=partner_id,
        cert_name="医疗器械生产许可证",
        cert_no=f"CERT-{partner_id}-{valid_until.isoformat()}",
        issuer="省药品监督管理局",
        valid_from=valid_until - timedelta(days=365),
        valid_until=valid_until,
        file_ref="/data/certs/x.pdf",
        audit_status="audited" if audited else "draft",
        audited_by="qa" if audited else None,
        audited_at=datetime.now() if audited else None,
    )
    db_session.add(cert)
    db_session.commit()
    return cert


# ---------- CRUD 与权限矩阵 ----------

def test_create_cert_admin_200(db_session, make_role_client, supplier):
    resp = make_role_client("admin").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audit_status"] == "draft"
    assert body["cert_no"] == "CERT-2026-001"
    assert body["audited_by"] is None


def test_create_cert_purchase_200(db_session, make_role_client, supplier):
    """purchase 角色可录入资质（partner_cert.write）。"""
    resp = make_role_client("purchase").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    )
    assert resp.status_code == 200, resp.text


def test_create_cert_qa_403(make_role_client, supplier):
    """qa 只可审核不可录入（partner_cert.write 未授权）。"""
    resp = make_role_client("qa").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    )
    assert resp.status_code == 403
    assert "partner_cert.write" in resp.json()["detail"]


def test_create_cert_warehouse_403(make_role_client, supplier):
    assert (
        make_role_client("warehouse")
        .post("/api/v1/partner-certs", json=make_cert_payload(supplier.id))
        .status_code
        == 403
    )


def test_create_cert_non_supplier_400(make_role_client, customer):
    """非 supplier 往来单位不可挂资质：400 中文错误。"""
    resp = make_role_client("admin").post(
        "/api/v1/partner-certs", json=make_cert_payload(customer.id)
    )
    assert resp.status_code == 400
    assert "供应商" in resp.json()["detail"]


def test_create_cert_missing_partner_404(make_role_client):
    resp = make_role_client("admin").post("/api/v1/partner-certs", json=make_cert_payload(9999))
    assert resp.status_code == 404


def test_update_draft_cert_200(db_session, make_role_client, supplier):
    admin = make_role_client("admin")
    cert = admin.post("/api/v1/partner-certs", json=make_cert_payload(supplier.id)).json()
    resp = admin.put(
        f"/api/v1/partner-certs/{cert['id']}",
        json={"cert_name": "营业执照（换证）", "valid_until": (date.today() + timedelta(days=730)).isoformat()},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["cert_name"] == "营业执照（换证）"
    assert resp.json()["audit_status"] == "draft"


def test_update_audited_cert_400(db_session, make_role_client, supplier):
    """已审核资质禁止修改（仅 draft 可改）。"""
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30))
    cert = make_role_client("admin").get(
        "/api/v1/partner-certs", params={"partner_id": supplier.id}
    ).json()["items"][0]
    resp = make_role_client("admin").put(
        f"/api/v1/partner-certs/{cert['id']}", json={"cert_name": "改名"}
    )
    assert resp.status_code == 400
    assert "草稿" in resp.json()["detail"]


def test_update_cert_qa_403(make_role_client, supplier):
    cert = make_role_client("admin").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    ).json()
    resp = make_role_client("qa").put(
        f"/api/v1/partner-certs/{cert['id']}", json={"cert_name": "x"}
    )
    assert resp.status_code == 403


def test_update_missing_cert_404(make_role_client):
    assert (
        make_role_client("admin").put("/api/v1/partner-certs/9999", json={}).status_code == 404
    )


# ---------- 审核流 ----------

def test_qa_audit_cert_200(db_session, make_role_client, supplier):
    """qa 审核：draft → audited，记录 audited_by / audited_at。"""
    cert = make_role_client("purchase").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    ).json()
    resp = make_role_client("qa").post(f"/api/v1/partner-certs/{cert['id']}/audit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audit_status"] == "audited"
    assert body["audited_by"] == "tester"
    assert body["audited_at"] is not None


def test_purchase_audit_403(make_role_client, supplier):
    """purchase 无 partner_cert.audit：403。"""
    cert = make_role_client("purchase").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    ).json()
    resp = make_role_client("purchase").post(f"/api/v1/partner-certs/{cert['id']}/audit")
    assert resp.status_code == 403
    assert "partner_cert.audit" in resp.json()["detail"]


def test_audit_twice_400(db_session, make_role_client, supplier):
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30))
    cert = make_role_client("admin").get(
        "/api/v1/partner-certs", params={"partner_id": supplier.id}
    ).json()["items"][0]
    resp = make_role_client("admin").post(f"/api/v1/partner-certs/{cert['id']}/audit")
    assert resp.status_code == 400


def test_audit_missing_cert_404(make_role_client):
    assert make_role_client("admin").post("/api/v1/partner-certs/9999/audit").status_code == 404


# ---------- 列表与临期预警 ----------

def test_list_filter_partner_and_status(db_session, make_role_client, supplier, customer):
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30))
    create_cert(db_session, customer.id, valid_until=date.today() + timedelta(days=30), audited=False)
    admin = make_role_client("admin")
    resp = admin.get("/api/v1/partner-certs", params={"partner_id": supplier.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["partner_id"] == supplier.id
    resp = admin.get("/api/v1/partner-certs", params={"audit_status": "audited"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["audit_status"] == "audited"


def test_list_pagination(db_session, make_role_client, supplier, customer):
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30))
    create_cert(db_session, customer.id, valid_until=date.today() + timedelta(days=30))
    admin = make_role_client("admin")
    body = admin.get("/api/v1/partner-certs", params={"page": 2, "page_size": 1}).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_expiring_list_default_30(db_session, make_role_client, supplier):
    """临期列表：+10 天 audited 命中；+40 天 / 已过期 / draft 不命中，含 partner 名。"""
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=10))
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=40))
    create_cert(db_session, supplier.id, valid_until=date.today() - timedelta(days=1))
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=5), audited=False)
    resp = make_role_client("admin").get("/api/v1/partner-certs/expiring")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["audit_status"] == "audited"
    assert item["partner_name"] == "测试供应商"
    assert item["valid_until"] == (date.today() + timedelta(days=10)).isoformat()
    assert item["days_left"] == 10


def test_expiring_days_param_45(db_session, make_role_client, supplier):
    """days=45 时 +40 天证照进入预警。"""
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=40))
    body = make_role_client("admin").get(
        "/api/v1/partner-certs/expiring", params={"days": 45}
    ).json()
    assert body["total"] == 1


def test_expiring_readable_by_all_roles(make_role_client):
    for role in ("admin", "purchase", "warehouse", "production", "qa"):
        resp = make_role_client(role).get("/api/v1/partner-certs/expiring")
        assert resp.status_code == 200, role


def test_expiring_days_must_be_positive(make_role_client):
    assert (
        make_role_client("admin")
        .get("/api/v1/partner-certs/expiring", params={"days": 0})
        .status_code
        == 422
    )


# ---------- PO 资质拦截 ----------

def test_po_create_blocked_without_cert(db_session, make_role_client, po_refs):
    """无任何资质：400「无有效资质」。"""
    resp = make_role_client("admin").post("/api/v1/purchase-orders", json=po_refs)
    assert resp.status_code == 400
    assert "无有效资质" in resp.json()["detail"]


def test_po_create_blocked_with_expired_cert(db_session, make_role_client, supplier, po_refs):
    """仅过期资质：400「资质已过期」。"""
    create_cert(db_session, supplier.id, valid_until=date.today() - timedelta(days=1))
    resp = make_role_client("admin").post("/api/v1/purchase-orders", json=po_refs)
    assert resp.status_code == 400
    assert "资质已过期" in resp.json()["detail"]


def test_po_create_blocked_with_draft_only_cert(db_session, make_role_client, supplier, po_refs):
    """草稿资质不生效：400「无有效资质」。"""
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30), audited=False)
    resp = make_role_client("admin").post("/api/v1/purchase-orders", json=po_refs)
    assert resp.status_code == 400
    assert "无有效资质" in resp.json()["detail"]


def test_audit_then_po_create_and_approve_ok(db_session, make_role_client, supplier, po_refs):
    """审核后资质生效：PO 建+审全通。"""
    cert = make_role_client("purchase").post(
        "/api/v1/partner-certs", json=make_cert_payload(supplier.id)
    ).json()
    assert make_role_client("qa").post(f"/api/v1/partner-certs/{cert['id']}/audit").status_code == 200
    admin = make_role_client("admin")
    po = admin.post("/api/v1/purchase-orders", json=po_refs)
    assert po.status_code == 200, po.text
    resp = admin.post(f"/api/v1/purchase-orders/{po.json()['id']}/approve")
    assert resp.status_code == 200, resp.text


def test_po_approve_blocked_after_cert_expired(db_session, make_role_client, supplier, po_refs):
    """审核时点拦截：建单时资质有效，审核前证照过期 → approve 400。"""
    create_cert(db_session, supplier.id, valid_until=date.today() + timedelta(days=30))
    admin = make_role_client("admin")
    po = admin.post("/api/v1/purchase-orders", json=po_refs).json()
    cert = db_session.scalar(select(PartnerCert).where(PartnerCert.partner_id == supplier.id))
    cert.valid_until = date.today() - timedelta(days=1)
    db_session.commit()
    resp = admin.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    assert resp.status_code == 400
    assert "资质已过期" in resp.json()["detail"]


def test_po_customer_partner_not_blocked(db_session, make_role_client, customer):
    """非 supplier 不校验资质：客户往来不拦截。"""
    wh = Warehouse(code="W2", name="二号仓")
    mat = Material(code="M2", name="铝锭", unit="kg", category=MaterialCategory.raw)
    db_session.add_all([wh, mat])
    db_session.commit()
    payload = {
        "partner_id": customer.id,
        "order_date": date.today().isoformat(),
        "lines": [
            {"material_id": mat.id, "qty": 5, "unit_price": 3, "warehouse_id": wh.id}
        ],
    }
    resp = make_role_client("admin").post("/api/v1/purchase-orders", json=payload)
    assert resp.status_code == 200, resp.text


def test_block_writes_operation_log(db_session, make_role_client, supplier, po_refs):
    """拦截事件写 operation_logs 便于追溯。"""
    create_cert(db_session, supplier.id, valid_until=date.today() - timedelta(days=1))
    make_role_client("admin").post("/api/v1/purchase-orders", json=po_refs)
    logs = db_session.scalars(
        select(OperationLog).where(OperationLog.action == "partner_cert_block")
    ).all()
    assert len(logs) == 1
    assert supplier.name in logs[0].detail
