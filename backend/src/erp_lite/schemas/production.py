"""生产主线 schema：BOM / MRP / 生产工单的入参与出参模型。"""

from decimal import Decimal

from pydantic import BaseModel, Field

from erp_lite.models.base import DocStatus


# ---------- BOM ----------


class BomLineIn(BaseModel):
    material_id: int
    qty_per: Decimal  # >0 校验在路由层做（中文 400）
    loss_pct: Decimal = Decimal("0")  # 0~100 校验在路由层做


class BomPut(BaseModel):
    lines: list[BomLineIn] = Field(default_factory=list)  # 空列表=清空 BOM


class BomMaterialBrief(BaseModel):
    id: int
    code: str
    name: str

    model_config = {"from_attributes": True}


class BomLineOut(BaseModel):
    id: int
    material_id: int
    qty_per: Decimal
    loss_pct: Decimal
    material: BomMaterialBrief

    model_config = {"from_attributes": True}


class BomOut(BaseModel):
    id: int
    material_id: int
    status: str
    material: BomMaterialBrief
    lines: list[BomLineOut]

    model_config = {"from_attributes": True}


# ---------- MRP ----------


class MrpDemandIn(BaseModel):
    material_id: int
    qty: Decimal = Field(gt=0)


class MrpRunIn(BaseModel):
    demand: list[MrpDemandIn] = Field(min_length=1)


class MrpSuggestionOut(BaseModel):
    id: int
    run_id: int
    material_id: int
    type: str
    net_qty: Decimal
    converted: bool
    converted_doc_id: int | None
    material: BomMaterialBrief

    model_config = {"from_attributes": True}


class MrpSuggestionPage(BaseModel):
    items: list[MrpSuggestionOut]
    total: int


class MrpConvertIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    partner_id: int | None = None


# ---------- 生产工单 ----------


class MoCreate(BaseModel):
    material_id: int
    planned_qty: Decimal = Field(gt=0)
    warehouse_id: int
    remark: str | None = Field(default=None, max_length=255)


class MoIssueLineIn(BaseModel):
    material_id: int
    qty: Decimal = Field(gt=0)
    batch_id: int | None = None  # v2 批次：批次物料领料必填（路由层校验）


class MoIssueIn(BaseModel):
    """领料：不传 lines 时按当前 BOM × qty/planned_qty 比例展开；qty 缺省=整单。"""

    lines: list[MoIssueLineIn] | None = None
    qty: Decimal | None = Field(default=None, gt=0)


class MoIssueOut(BaseModel):
    id: int
    material_id: int
    qty: Decimal
    cost_amount: Decimal

    model_config = {"from_attributes": True}


class MoOut(BaseModel):
    id: int
    doc_no: str
    status: DocStatus
    material_id: int
    planned_qty: Decimal
    warehouse_id: int
    issued_qty_cost: Decimal
    transferred_cost: Decimal
    extra_cost: Decimal
    # v3：完工量持久化（修「UI 完工量恒 0 / 二次完工必超发」断点）+ 拒绝原因
    received_qty: Decimal = Decimal("0")
    completed_qty: Decimal = Decimal("0")
    reject_reason: str | None = None
    remark: str | None
    created_by: str | None
    material: BomMaterialBrief

    model_config = {"from_attributes": True}


class MoDetail(MoOut):
    issues: list[MoIssueOut]


class MoReceiveIn(BaseModel):
    qty: Decimal = Field(gt=0)
    extra_cost: Decimal = Field(default=Decimal("0"), ge=0)


class MoUnissueIn(BaseModel):
    """领料退回（v3）：按 (物料, 批次) 退回指定数量，成本按原领料单价冲回。"""

    material_id: int
    qty: Decimal = Field(gt=0)
    batch_id: int | None = None  # 批次物料必填（与领料口径一致）
