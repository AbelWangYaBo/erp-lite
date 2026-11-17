"""数据库 engine / session 管理。DATABASE_URL 从环境变量读取。"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from erp_lite.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
