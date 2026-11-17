"""模型公共设施：Base、状态枚举、时间戳/单据字段 Mixin。"""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DocStatus(str, enum.Enum):
    """单据状态机（字符串存储，便于审计可读）。"""

    draft = "draft"
    approved = "approved"
    partial = "partial"
    completed = "completed"
    closed = "closed"
    void = "void"


class MaterialCategory(str, enum.Enum):
    raw = "raw"
    semi = "semi"
    finished = "finished"


class PartnerType(str, enum.Enum):
    supplier = "supplier"
    customer = "customer"


class MoveType(str, enum.Enum):
    purchase_in = "purchase_in"
    sale_out = "sale_out"
    mo_issue = "mo_issue"
    mo_receipt = "mo_receipt"
    adjust = "adjust"


class SettleStatus(str, enum.Enum):
    open = "open"
    partial = "partial"
    settled = "settled"


class InspStatus(str, enum.Enum):
    """批次检验状态（GMP 放行控制：只有 qualified 可领料/出库）。"""

    pending = "pending"
    qualified = "qualified"
    rejected = "rejected"
    closed = "closed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocMixin(TimestampMixin):
    """单据公共字段：id / doc_no / status / remark / created_by + 审计时间戳。"""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        SAEnum(DocStatus, native_enum=False, length=16, values_callable=lambda e: [m.value for m in e]),
        default=DocStatus.draft,
        nullable=False,
    )
    remark: Mapped[str | None] = mapped_column(String(255), default=None)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)


def str_enum(values: type[enum.Enum], length: int = 16) -> SAEnum:
    """字符串存储的可移植枚举列（VARCHAR + CHECK，两种方言通用）。"""
    return SAEnum(
        values,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )
