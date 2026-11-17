"""销售：销售订单 + 行。"""

from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, DocMixin


class SalesOrder(Base, DocMixin):
    __tablename__ = "sales_orders"

    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), nullable=False)
    order_date: Mapped[date] = mapped_column(Date, nullable=False)

    partner = relationship("Partner")
    lines = relationship(
        "SalesOrderLine", back_populates="order", cascade="all, delete-orphan"
    )


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
