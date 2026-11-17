"""审计：字段级留痕 / 全局操作日志 / 单据编号序列。"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column


from erp_lite.models.base import Base


class AuditRecord(Base):
    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    doc_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # create/update/status/void/close
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64), default=None)
    old_value: Mapped[str | None] = mapped_column(String(255), default=None)
    new_value: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class DocSequence(Base):
    __tablename__ = "doc_sequences"
    __table_args__ = (UniqueConstraint("prefix", "seq_date", name="uq_doc_seq_prefix_date"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    seq_date: Mapped[str] = mapped_column(String(8), nullable=False)  # YYYYMMDD
    last_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
