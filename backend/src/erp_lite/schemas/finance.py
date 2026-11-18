"""财务 schema：应付/应收列表、核销请求与出参、对账单。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from erp_lite.models.base import SettleStatus


# ---------- 应付 / 应收列表 ----------


class PayableOut(BaseModel):
    id: int
    partner_id: int
    partner_name: str
    source_type: str
    source_id: int
    source_no: str | None = None  # v3：来源单号（DocLink 可点回 PO/SO）
    amount: Decimal
    settled_amount: Decimal
    status: SettleStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class ReceivableOut(PayableOut):
    pass


class PayablePage(BaseModel):
    items: list[PayableOut]
    total: int


class ReceivablePage(BaseModel):
    items: list[ReceivableOut]
    total: int


# ---------- 核销 ----------


class SettlementLineIn(BaseModel):
    target_type: Literal["ap", "ar"]
    target_id: int
    amount: Decimal = Field(gt=0)


class SettlementCreate(BaseModel):
    type: Literal["receipt", "payment"]
    partner_id: int
    date: date
    remark: str | None = Field(default=None, max_length=255)
    lines: list[SettlementLineIn] = Field(min_length=1)


class SettlementLineOut(BaseModel):
    id: int
    target_type: str
    target_id: int
    amount: Decimal

    model_config = {"from_attributes": True}


class SettlementOut(BaseModel):
    id: int
    type: str
    partner_id: int
    partner_name: str
    amount: Decimal
    date: date
    remark: str | None
    reversed_settlement_id: int | None = None  # v3：冲回单指向被冲回单
    created_by: str | None
    created_at: datetime
    lines: list[SettlementLineOut]

    model_config = {"from_attributes": True}


class SettlementPage(BaseModel):
    items: list[SettlementOut]
    total: int


class SettlementReverseIn(BaseModel):
    """核销冲回请求（v3）：生成红字冲回单，原记录不删。"""

    date: date
    remark: str | None = Field(default=None, max_length=255)


# ---------- 对账单 ----------


class StatementDetailOut(BaseModel):
    """期间内该往来单位的应付/应收明细。"""

    target_type: Literal["ap", "ar"]
    target_id: int
    source_type: str
    source_id: int
    source_no: str
    date: date
    amount: Decimal
    settled_amount: Decimal
    balance: Decimal


class StatementSettlementOut(BaseModel):
    """期间内该往来单位的收付款记录（is_reverse=True 为冲回单，参与负核销）。"""

    id: int
    type: str
    date: date
    amount: Decimal
    is_reverse: bool = False
    remark: str | None


class StatementTotalsOut(BaseModel):
    total_amount: Decimal
    total_settled: Decimal
    total_balance: Decimal
    period_new_amount: Decimal = Decimal("0")
    settlement_total: Decimal


class StatementOut(BaseModel):
    """对账单（v3 勾稽口径）：期初未清 + 期间新增 − 期间核销 = 期末未清（恒等式成立）。

    - details：期间新增账单明细；
    - settlements：期间核销记录（冲回单计负核销）；
    - opening_balance：期初未清（由恒等式倒推）；closing_balance：期末未清
      （= Σ 全部账单 amount − settled，现状事实）。
    """

    partner_id: int
    partner_name: str
    partner_type: str
    target_type: Literal["ap", "ar"]
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    opening_balance: Decimal
    closing_balance: Decimal = Decimal("0")
    details: list[StatementDetailOut]
    settlements: list[StatementSettlementOut]
    totals: StatementTotalsOut

    model_config = {"populate_by_name": True}
