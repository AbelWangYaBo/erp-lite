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
    created_by: str | None
    created_at: datetime
    lines: list[SettlementLineOut]

    model_config = {"from_attributes": True}


class SettlementPage(BaseModel):
    items: list[SettlementOut]
    total: int


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
    """期间内该往来单位的收付款记录。"""

    id: int
    type: str
    date: date
    amount: Decimal
    remark: str | None


class StatementTotalsOut(BaseModel):
    total_amount: Decimal
    total_settled: Decimal
    total_balance: Decimal
    settlement_total: Decimal


class StatementOut(BaseModel):
    """对账单（v1 简化口径）：

    - details：期间内该 partner 的应付/应收明细（按 created_at 日期落期间）；
    - settlements：期间收付款记录（按结算单 date 落期间）；
    - opening_balance：期初（from 之前发生、至今仍未核销结清的余额合计），
      不做会计意义的严格期初结转；
    - totals：期间明细的金额/已核销/余额合计与期间收付款合计。
    """

    partner_id: int
    partner_name: str
    partner_type: str
    target_type: Literal["ap", "ar"]
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    opening_balance: Decimal
    details: list[StatementDetailOut]
    settlements: list[StatementSettlementOut]
    totals: StatementTotalsOut

    model_config = {"populate_by_name": True}
