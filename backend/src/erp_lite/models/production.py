"""生产主线：BOM / MRP / 生产工单。"""

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, DocMixin


class Bom(Base):
    __tablename__ = "boms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    material_id: Mapped[int] = mapped_column(
        ForeignKey("materials.id"), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)

    material = relationship("Material")
    lines = relationship("BomLine", back_populates="bom", cascade="all, delete-orphan")


class BomLine(Base):
    __tablename__ = "bom_lines"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bom_id: Mapped[int] = mapped_column(ForeignKey("boms.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    qty_per: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    loss_pct: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    bom = relationship("Bom", back_populates="lines")
    material = relationship("Material")


class MrpRun(Base):
    __tablename__ = "mrp_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    demand_type: Mapped[str] = mapped_column(String(16), nullable=False)  # manual / sales
    params_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    result_json: Mapped[dict | None] = mapped_column(JSON, default=None)

    suggestions = relationship(
        "MrpSuggestion", back_populates="run", cascade="all, delete-orphan"
    )


class MrpSuggestion(Base):
    __tablename__ = "mrp_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("mrp_runs.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(8), nullable=False)  # buy / make
    net_qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    converted_doc_id: Mapped[int | None] = mapped_column(default=None)

    run = relationship("MrpRun", back_populates="suggestions")
    material = relationship("Material")


class ManufacturingOrder(Base, DocMixin):
    __tablename__ = "manufacturing_orders"

    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    planned_qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"), nullable=False)
    issued_qty_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    transferred_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    extra_cost: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    # v3：完工量持久化（修「UI 完工量恒 0」）；拒绝原因留痕
    received_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    completed_qty: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(255), default=None)

    material = relationship("Material")
    warehouse = relationship("Warehouse")
    issues = relationship(
        "MoIssue", back_populates="order", cascade="all, delete-orphan"
    )


class MoIssue(Base):
    __tablename__ = "mo_issues"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mo_id: Mapped[int] = mapped_column(ForeignKey("manufacturing_orders.id"), nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    cost_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)

    order = relationship("ManufacturingOrder", back_populates="issues")
    material = relationship("Material")
