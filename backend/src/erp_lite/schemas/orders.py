"""进销存 schema：采购/销售订单、出入库请求与库存查询出参。"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from erp_lite.models.base import DocStatus, MoveType


# ---------- 订单行 ----------


class OrderLineCreate(BaseModel):
    material_id: int
    qty: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    warehouse_id: int


class OrderLineOut(BaseModel):
    id: int
    material_id: int
    qty: Decimal
    unit_price: Decimal
    warehouse_id: int

    model_config = {"from_attributes": True}


class PurchaseOrderLineOut(OrderLineOut):
    received_qty: Decimal


class SalesOrderLineOut(OrderLineOut):
    shipped_qty: Decimal


# ---------- 采购订单 ----------


class PurchaseOrderCreate(BaseModel):
    partner_id: int
    order_date: date
    expected_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    lines: list[OrderLineCreate] = Field(min_length=1)


class PurchaseOrderUpdate(BaseModel):
    partner_id: int | None = None
    order_date: date | None = None
    expected_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    lines: list[OrderLineCreate] | None = None


class PurchaseOrderOut(BaseModel):
    id: int
    doc_no: str
    status: DocStatus
    partner_id: int
    order_date: date
    expected_date: date | None
    remark: str | None
    created_by: str | None
    lines: list[PurchaseOrderLineOut]

    model_config = {"from_attributes": True}


class AuditOut(BaseModel):
    id: int
    action: str
    actor: str
    field: str | None
    old_value: str | None
    new_value: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PurchaseOrderDetail(PurchaseOrderOut):
    audits: list[AuditOut] = []


# ---------- 销售订单 ----------


class SalesOrderCreate(BaseModel):
    partner_id: int
    order_date: date
    remark: str | None = Field(default=None, max_length=255)
    lines: list[OrderLineCreate] = Field(min_length=1)


class SalesOrderUpdate(BaseModel):
    partner_id: int | None = None
    order_date: date | None = None
    remark: str | None = Field(default=None, max_length=255)
    lines: list[OrderLineCreate] | None = None


class SalesOrderOut(BaseModel):
    id: int
    doc_no: str
    status: DocStatus
    partner_id: int
    order_date: date
    remark: str | None
    created_by: str | None
    lines: list[SalesOrderLineOut]

    model_config = {"from_attributes": True}


class SalesOrderDetail(SalesOrderOut):
    audits: list[AuditOut] = []


# ---------- 入库 / 出库请求 ----------


class ReceiveLineIn(BaseModel):
    line_id: int
    qty: Decimal = Field(gt=0)
    warehouse_id: int
    # v2 批次：采购入库传 batch_no（批次物料必填，路由层校验）；
    # 销售出库传 batch_id（批次物料必填）。两者均可空，非批次物料走 v1 路径。
    batch_no: str | None = Field(default=None, max_length=64)
    batch_id: int | None = None


# ---------- 库存查询 ----------


class StockOut(BaseModel):
    material_id: int
    material_code: str
    material_name: str
    qty: Decimal
    avg_cost: Decimal | None
    amount: Decimal


class MoveOut(BaseModel):
    id: int
    material_id: int
    warehouse_id: int
    move_type: MoveType
    qty: Decimal
    unit_cost: Decimal
    amount: Decimal
    source_type: str
    source_id: int
    source_line_id: int | None
    moved_at: datetime

    model_config = {"from_attributes": True}


class MovePage(BaseModel):
    items: list[MoveOut]
    total: int
