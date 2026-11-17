"""供应商资质 schema（V2-S4）：入参/出参模型。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from erp_lite.models.base import CertStatus


class PartnerCertCreate(BaseModel):
    partner_id: int
    cert_name: str = Field(min_length=1, max_length=128)
    cert_no: str = Field(min_length=1, max_length=64)
    issuer: str | None = Field(default=None, max_length=128)
    valid_from: date
    valid_until: date
    file_ref: str | None = Field(default=None, max_length=255)


class PartnerCertUpdate(BaseModel):
    """草稿资质可改字段（partner_id 不可改：换供应商请重录）。"""

    cert_name: str | None = Field(default=None, min_length=1, max_length=128)
    cert_no: str | None = Field(default=None, min_length=1, max_length=64)
    issuer: str | None = Field(default=None, max_length=128)
    valid_from: date | None = None
    valid_until: date | None = None
    file_ref: str | None = Field(default=None, max_length=255)


class PartnerCertOut(BaseModel):
    id: int
    partner_id: int
    partner_name: str | None = None
    cert_name: str
    cert_no: str
    issuer: str | None
    valid_from: date
    valid_until: date
    file_ref: str | None
    audit_status: CertStatus
    audited_by: str | None
    audited_at: datetime | None

    model_config = {"from_attributes": True}


class PartnerCertPage(BaseModel):
    items: list[PartnerCertOut]
    total: int


class ExpiringCertOut(PartnerCertOut):
    days_left: int


class ExpiringCertPage(BaseModel):
    items: list[ExpiringCertOut]
    total: int
