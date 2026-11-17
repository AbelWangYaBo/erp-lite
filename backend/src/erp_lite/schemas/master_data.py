"""主数据 schema：物料 / 仓库 / 往来单位的入参与出参模型。"""

from pydantic import BaseModel, Field

from erp_lite.models.base import MaterialCategory, PartnerType


# ---------- 仓库 ----------


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=64)


class WarehouseUpdate(WarehouseCreate):
    pass


class WarehouseOut(BaseModel):
    id: int
    code: str
    name: str

    model_config = {"from_attributes": True}


class WarehousePage(BaseModel):
    items: list[WarehouseOut]
    total: int


# ---------- 物料 ----------


class MaterialCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    spec: str | None = Field(default=None, max_length=128)
    unit: str = Field(min_length=1, max_length=16)
    category: MaterialCategory
    default_warehouse_id: int | None = None
    batch_required: bool = False  # v2 批次管理开关
    udi_di: str | None = Field(default=None, max_length=64)  # UDI-DI（成品）
    udi_pi_rule: str | None = Field(default=None, max_length=128)  # PI 组成规则（成品）


class MaterialUpdate(MaterialCreate):
    pass


class MaterialOut(BaseModel):
    id: int
    code: str
    name: str
    spec: str | None
    unit: str
    category: MaterialCategory
    default_warehouse_id: int | None
    batch_required: bool
    udi_di: str | None
    udi_pi_rule: str | None

    model_config = {"from_attributes": True}


class MaterialPage(BaseModel):
    items: list[MaterialOut]
    total: int


# ---------- 往来单位 ----------


class PartnerCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    type: PartnerType
    contact: str | None = Field(default=None, max_length=128)


class PartnerUpdate(PartnerCreate):
    pass


class PartnerOut(BaseModel):
    id: int
    code: str
    name: str
    type: PartnerType
    contact: str | None

    model_config = {"from_attributes": True}


class PartnerPage(BaseModel):
    items: list[PartnerOut]
    total: int
