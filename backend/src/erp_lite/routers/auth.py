"""auth 路由：登录（users 表空时自动播种管理员）签发 JWT。"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.config import settings
from erp_lite.database import get_db
from erp_lite.deps import create_access_token
from erp_lite.models import User
from erp_lite.services.audit import write_op_log
from erp_lite.services.security import hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    token: str


def _seed_admin_if_empty(db: Session) -> None:
    """users 表为空时按配置播种管理员（首次登录自动完成）。"""
    count = db.scalar(select(func.count(User.id)))
    if count == 0:
        db.add(
            User(
                username=settings.erp_admin_user,
                password_hash=hash_password(settings.erp_admin_password),
            )
        )
        db.commit()


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    _seed_admin_if_empty(db)
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    write_op_log(db, payload.username, "login", "登录成功")
    db.commit()
    return TokenResponse(token=create_access_token(user.username))
