"""采购：采购订单 + 行。"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, DocMixin


class PurchaseOrder(Base, DocMixin):
    __tablename__ = "purchase_orders"

    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_date: Mapped[date | None] = mapped_column(Date, default=None)

    partner = relationship("Partner")
    lines = relationship(
        "PurchaseOrderLine", back_populates="order", cascade="all, delete-orphan"
    )


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
