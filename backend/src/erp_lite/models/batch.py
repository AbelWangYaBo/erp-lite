"""批次台账（GMP 追溯核心）：一条批次记录贯穿来源/检验/处置。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_lite.models.base import Base, InspStatus, str_enum


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # purchase_order / manufacturing_order
    source_id: Mapped[int] = mapped_column(nullable=False)
    supplier_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id"), default=None
    )  # 原料批次记来源供应商，成品批次为空
    insp_status: Mapped[InspStatus] = mapped_column(
        str_enum(InspStatus), default=InspStatus.pending, nullable=False
    )
    disposition: Mapped[str | None] = mapped_column(String(16), default=None)  # return / destroy
    disposition_remark: Mapped[str | None] = mapped_column(String(255), default=None)
    # v3：报废/费用化损失金额（批次级口径，含已结转生产成本说明）
    loss_amount: Mapped[float] = mapped_column(Numeric(18, 4), default=0, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    material = relationship("Material")
    supplier = relationship("Partner")


class InspectionRecord(Base):
    """检验记录（v3 双段制）：qc 录入检项+建议结论，qa 复核判定；复判新开记录，历史不覆盖。"""

    __tablename__ = "inspection_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False)
    items_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # [{name, result}]
    suggest_conclusion: Mapped[str] = mapped_column(String(8), nullable=False)  # qc 建议 pass/fail
    recorder: Mapped[str | None] = mapped_column(String(64), default=None)  # qc 录入人
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    remark: Mapped[str | None] = mapped_column(String(255), default=None)
    # qa 复核段：录入后待判定时 conclusion 为空
    conclusion: Mapped[str | None] = mapped_column(String(8), default=None)  # 最终 pass/fail
    judge_by: Mapped[str | None] = mapped_column(String(64), default=None)
    judged_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    batch = relationship("Batch")

    @property
    def items(self) -> list:
        """检验项明细（响应字段名 items，落库列为 items_json）。"""
        return self.items_json
