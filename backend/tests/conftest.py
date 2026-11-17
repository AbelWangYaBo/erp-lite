"""测试基建：SQLite 内存库 + DependencyOverrides 测试替身。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.main import app
from erp_lite.models import Base


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
    fake_user = SimpleNamespace(id=1, username="tester")
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
