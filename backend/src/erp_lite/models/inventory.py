"""库存台账：每次出入库一条流水（数量带符号）。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, MoveType, str_enum


class InventoryMove(Base):
    __tablename__ = "inventory_moves"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    move_type: Mapped[MoveType] = mapped_column(str_enum(MoveType), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)  # 入库正 / 出库负
    unit_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(nullable=False)
    source_line_id: Mapped[int | None] = mapped_column(default=None)
    moved_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    material = relationship("Material")
    warehouse = relationship("Warehouse")
