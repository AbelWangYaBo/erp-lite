"""认证依赖：JWT 解析（含 role claim）+ 当前用户 + require_action 权限工厂。"""

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.config import settings
from erp_lite.database import get_db
from erp_lite.models import User
from erp_lite.services.rbac import actions_for

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def create_access_token(username: str, role: str | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload: dict = {"sub": username, "exp": expire}
    if role is not None:
        payload["role"] = role
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        username: str | None = payload.get("sub")
        # v1 旧 token 无 role claim：拒绝（不静默回退 admin），要求重新登录
        role_claim: str | None = payload.get("role")
        if not username or not role_claim:
            raise credentials_exc
    except JWTError:
        raise credentials_exc
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        raise credentials_exc
    return user


def require_action(action: str):
    """依赖工厂：当前用户的 role 须持有 action，否则 403（中文提示）。"""

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if action not in actions_for(current_user.role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"当前角色无权执行该操作：{action}",
            )
        return current_user

    return dependency


def ensure_not_self(current_user: User, creator: str | None, action_label: str) -> None:
    """规则 A｜不得自审（DESIGN-v3 §2）：审核/判定/撤销类动作的操作人
    不得是单据创建/录入人；admin 无豁免（演示职责分离）。"""
    if creator and current_user.username == creator:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{action_label}不允许自审：操作人须与创建/录入人不同",
        )
