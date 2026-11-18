"""库存台账：每次出入库一条流水（数量带符号）+ 盘点单（v3）。"""

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
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id"), default=None
    )  # 非批次物料为空，v1 数据合法
    moved_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    material = relationship("Material")
    warehouse = relationship("Warehouse")
    batch = relationship("Batch")


class StockCount(Base):
    """盘点单（v3）：warehouse 建单录实盘，finance/admin post 生成带批次 adjust 流水。"""

    __tablename__ = "stock_counts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_no: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)  # draft/posted
    remark: Mapped[str | None] = mapped_column(String(255), default=None)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    posted_by: Mapped[str | None] = mapped_column(String(64), default=None)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    warehouse = relationship("Warehouse")
    lines = relationship(
        "StockCountLine", back_populates="count", cascade="all, delete-orphan"
    )


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    count_id: Mapped[int] = mapped_column(ForeignKey("stock_counts.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id"), default=None)
    system_qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)  # 建单时账面快照
    counted_qty: Mapped[float | None] = mapped_column(Numeric(18, 4), default=None)  # 实盘（可后录）
    diff_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    count = relationship("StockCount", back_populates="lines")
    material = relationship("Material")
    batch = relationship("Batch")
