"""批次台账 schema：列表/详情出参（详情含批次级现存量与均价）。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from erp_lite.models.base import InspStatus


class BatchOut(BaseModel):
    id: int
    batch_no: str
    material_id: int
    source_type: str
    source_id: int
    supplier_id: int | None
    insp_status: InspStatus
    disposition: str | None
    disposition_remark: str | None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchDetail(BatchOut):
    stock_qty: Decimal
    stock_amount: Decimal
    avg_cost: Decimal | None
    material_code: str
    material_name: str


class BatchPage(BaseModel):
    items: list[BatchOut]
    total: int
