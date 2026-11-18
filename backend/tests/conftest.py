"""测试基建：SQLite 内存库 + DependencyOverrides 测试替身。"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.main import app
from erp_lite.models import Base, CertStatus, PartnerCert


@pytest.fixture()
def db_session():
    """SQLite 内存库（StaticPool 共享同一连接），建全部表。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """已登录替身的客户端：get_db 指向测试库，get_current_user 返回假用户。"""
    fake_user = SimpleNamespace(id=1, username="tester", role="admin")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_session):
    """未覆盖 get_current_user 的客户端：走真实鉴权逻辑（用于登录/401 测试）。"""
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def switch_user(client: TestClient, username: str, role: str = "admin") -> None:
    """v3 规则 A（不得自审）适配：切换当前用户身份（重新覆盖 get_current_user）。

    审批/判定/审核类动作要求操作人 ≠ 创建/录入人，且 admin 无豁免；
    流程测试用 tester 创建、switch_user(client, "approver") 换人审核。
    """
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=hash(username) % 100000, username=username, role=role
    )


def grant_supplier_cert(db, partner_id: int) -> None:
    """给供应商补一张 audited 且未过期的资质证照。

    V2-S4 资质拦截的语义适配：v1 既有 PO 流程测试的供应商默认资质合规，
    业务意图（采购全链路）不变；拦截行为本身由 test_partner_certs.py 专测。
    """
    today = date.today()
    db.add(
        PartnerCert(
            partner_id=partner_id,
            cert_name="测试资质",
            cert_no=f"CERT-{partner_id}-{today.isoformat()}",
            issuer="测试发证机关",
            valid_from=today - timedelta(days=365),
            valid_until=today + timedelta(days=365),
            file_ref="/tmp/cert.pdf",
            audit_status=CertStatus.audited,
            audited_by="tester",
            audited_at=datetime.now(),
        )
    )
    db.commit()
