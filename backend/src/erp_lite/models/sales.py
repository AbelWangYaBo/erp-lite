"""销售：销售订单 + 行 + 销售退货单（v3）。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, DocMixin


class SalesOrder(Base, DocMixin):
    __tablename__ = "sales_orders"

    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(255), default=None)  # v3：审批拒绝原因

    partner = relationship("Partner")
    lines = relationship(
        "SalesOrderLine", back_populates="order", cascade="all, delete-orphan"
    )


class SalesReturn(Base):
    """销售退货单（v3）：红字出库流水 + 批次回 pending 重检 + 冲应收联动。"""

    __tablename__ = "sales_returns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)  # 冲减应收金额（正数）
    reason: Mapped[str | None] = mapped_column(String(255), default=None)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    batch = relationship("Batch")
    order = relationship("SalesOrder")


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    so_id: Mapped[int] = mapped_column(ForeignKey("sales_orders.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    shipped_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)

    order = relationship("SalesOrder", back_populates="lines")
    material = relationship("Material")
    warehouse = relationship("Warehouse")
