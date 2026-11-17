"""主数据：物料 / 仓库 / 往来单位。"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, MaterialCategory, PartnerType, str_enum


class Warehouse(Base):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    spec: Mapped[str | None] = mapped_column(String(128), default=None)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[MaterialCategory] = mapped_column(str_enum(MaterialCategory), nullable=False)
    default_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id"), default=None
    )

    default_warehouse: Mapped["Warehouse | None"] = relationship()


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[PartnerType] = mapped_column(str_enum(PartnerType), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(128), default=None)
