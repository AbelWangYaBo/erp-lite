"""财务：应付 / 应收 / 收付款核销。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, SettleStatus, str_enum


class Payable(Base):
    __tablename__ = "payables"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    settled_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    status: Mapped[SettleStatus] = mapped_column(
        str_enum(SettleStatus), default=SettleStatus.open, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    partner = relationship("Partner")


class Receivable(Base):
    __tablename__ = "receivables"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    settled_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    status: Mapped[SettleStatus] = mapped_column(
        str_enum(SettleStatus), default=SettleStatus.open, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    partner = relationship("Partner")


class Settlement(Base):
    __tablename__ = "settlements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # receipt / payment
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), default=None)
    # v3：冲回单指向被冲回的核销单（红字核销，原记录不删）
    reversed_settlement_id: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    partner = relationship("Partner")
    lines = relationship(
        "SettlementLine", back_populates="settlement", cascade="all, delete-orphan"
    )


class SettlementLine(Base):
    __tablename__ = "settlement_lines"
    __table_args__ = (UniqueConstraint("id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("settlements.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(8), nullable=False)  # ap / ar
    target_id: Mapped[int] = mapped_column(nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    settlement = relationship("Settlement", back_populates="lines")
