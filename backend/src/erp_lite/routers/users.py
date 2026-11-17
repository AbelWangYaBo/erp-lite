"""用户与角色管理（v2 RBAC）：仅 admin（users.manage）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import User
from erp_lite.models.users import Role
from erp_lite.services.audit import write_op_log
from erp_lite.services.security import hash_password

router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    dependencies=[Depends(get_current_user), Depends(require_action("users.manage"))],
)


class UserCreate(BaseModel):
    username: str
    password: str
    role: Role


class RoleUpdate(BaseModel):
    role: Role


class UserOut(BaseModel):
    id: int
    username: str
    role: Role
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("")
def list_users(db: Session = Depends(get_db)) -> dict:
    users = db.scalars(select(User).order_by(User.id)).all()
    items = [UserOut.model_validate(u).model_dump(mode="json") for u in users]
    return {"items": items, "total": len(items)}


@router.post("", response_model=UserOut)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    if db.scalar(select(User).where(User.username == payload.username)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")
    if not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="密码不能为空")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    write_op_log(
        db, current_user.username, "user_create",
        f"创建用户：{user.username}（角色 {user.role.value}）",
    )
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}/role", response_model=UserOut)
def update_user_role(
    user_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    old_role = user.role
    user.role = payload.role
    write_op_log(
        db, current_user.username, "user_role_update",
        f"调整角色：{user.username} {old_role.value if hasattr(old_role, 'value') else old_role}"
        f" → {payload.role.value}（需重新登录生效）",
    )
    db.commit()
    db.refresh(user)
    return user
