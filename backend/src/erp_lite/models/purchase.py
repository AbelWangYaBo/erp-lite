"""采购：采购订单 + 行 + 采购退货单（v3）。"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, DocMixin


class PurchaseOrder(Base, DocMixin):
    __tablename__ = "purchase_orders"

    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date, default=None)
    reject_reason: Mapped[str | None] = mapped_column(String(255), default=None)  # v3：审批拒绝原因

    partner = relationship("Partner")
    lines = relationship(
        "PurchaseOrderLine", back_populates="order", cascade="all, delete-orphan"
    )


class PurchaseReturn(Base):
    """采购退货单（v3 处置闭环）：rejected 批次处置(return) 生成，红字流水+冲应付联动。"""

    __tablename__ = "purchase_returns"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)  # 冲减应付金额（正数）
    reason: Mapped[str | None] = mapped_column(String(255), default=None)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    batch = relationship("Batch")
    order = relationship("PurchaseOrder")


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    received_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)

    order = relationship("PurchaseOrder", back_populates="lines")
    material = relationship("Material")
    warehouse = relationship("Warehouse")
