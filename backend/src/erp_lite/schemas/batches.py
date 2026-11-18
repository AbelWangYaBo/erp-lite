"""批次台账 schema：列表/详情出参（详情含批次级现存量与均价）+ S3 检验/追溯。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

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
    loss_amount: Decimal = Decimal("0")
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


# ---------- v3：双段检验（qc 录入 / qa 判定）/ 处置 / 追溯 ----------


class InspectionItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    result: str = Field(min_length=1, max_length=64)


class InspectIn(BaseModel):
    """qc 段：录入检项与建议结论，不改变批次状态。"""

    items: list[InspectionItemIn] = Field(min_length=1)
    suggest_conclusion: Literal["pass", "fail"]
    remark: str | None = Field(default=None, max_length=255)


class JudgeIn(BaseModel):
    """qa 段：复核判定（操作人 ≠ qc 录入人）。"""

    conclusion: Literal["pass", "fail"]


class InspectionRecordOut(BaseModel):
    model_config = {"from_attributes": True}  # 路由层直接 model_validate ORM 记录

    id: int
    batch_id: int
    items: list[dict]
    suggest_conclusion: str
    recorder: str | None
    inspected_at: datetime
    remark: str | None
    conclusion: str | None
    judge_by: str | None
    judged_at: datetime | None


class DisposeIn(BaseModel):
    disposition: Literal["return", "destroy"]
    remark: str | None = Field(default=None, max_length=255)


class TraceBatchBrief(BaseModel):
    id: int
    batch_no: str
    material_id: int
    insp_status: InspStatus
    source_type: str
    source_id: int
    supplier_id: int | None


class TraceLink(BaseModel):
    via_doc: str
    to_batch: TraceBatchBrief
    partner: str | None
    qty: Decimal


class TraceOut(BaseModel):
    batch: TraceBatchBrief
    links: list[TraceLink]
