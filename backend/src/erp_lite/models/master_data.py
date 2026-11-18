"""主数据：物料 / 仓库 / 往来单位。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import (
    Base,
    CertStatus,
    MaterialCategory,
    PartnerType,
    str_enum,
)


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
    # v2 批次管理：启用后出入库必须带批次；UDI 仅成品要求
    batch_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    udi_di: Mapped[str | None] = mapped_column(String(64), default=None)
    udi_pi_rule: Mapped[str | None] = mapped_column(String(128), default=None)

    default_warehouse: Mapped["Warehouse | None"] = relationship()


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[PartnerType] = mapped_column(str_enum(PartnerType), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(128), default=None)


class PartnerCert(Base):
    """供应商资质证照（V2-S4 GMP 采购控制）：仅 supplier 可挂，审核后用于 PO 拦截。"""

    __tablename__ = "partner_certs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partners.id"), index=True, nullable=False
    )
    cert_name: Mapped[str] = mapped_column(String(128), nullable=False)
    cert_no: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(128), default=None)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    file_ref: Mapped[str | None] = mapped_column(String(255), default=None)  # 扫描件路径引用
    audit_status: Mapped[CertStatus] = mapped_column(
        str_enum(CertStatus), default=CertStatus.draft, nullable=False
    )
    audited_by: Mapped[str | None] = mapped_column(String(64), default=None)
    audited_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # v3：录入人（不得自审依据）+ 撤销（audited → revoked）
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    revoke_reason: Mapped[str | None] = mapped_column(String(255), default=None)
    revoked_by: Mapped[str | None] = mapped_column(String(64), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    partner = relationship("Partner")
