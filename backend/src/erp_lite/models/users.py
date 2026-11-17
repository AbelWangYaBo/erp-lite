"""users 表：账号 + 角色枚举（v2 RBAC）。"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from erp_lite.models.base import Base


class Role(str, enum.Enum):
    """系统角色（GMP 岗位职责分离，见 PRD-v2 §2.5）。"""

    admin = "admin"
    purchase = "purchase"
    warehouse = "warehouse"
    production = "production"
    qa = "qa"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[Role] = mapped_column(
        SAEnum(
            Role,
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=Role.admin,
        server_default=Role.admin.value,  # 存量用户迁移后均为 admin
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
