"""批次台账（GMP 追溯核心）：一条批次记录贯穿来源/检验/处置。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
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
    created_by: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    material = relationship("Material")
    supplier = relationship("Partner")
