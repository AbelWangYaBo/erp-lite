"""Pydantic v2 schema 包：与 routers/docs 契约对应。"""

from erp_lite.schemas.master_data import (
    MaterialCreate,
    MaterialOut,
    MaterialPage,
    MaterialUpdate,
    PartnerCreate,
    PartnerOut,
    PartnerPage,
    PartnerUpdate,
    WarehouseCreate,
    WarehouseOut,
    WarehousePage,
    WarehouseUpdate,
)

__all__ = [
    "MaterialCreate",
    "MaterialUpdate",
    "MaterialOut",
    "MaterialPage",
    "WarehouseCreate",
    "WarehouseUpdate",
    "WarehouseOut",
    "WarehousePage",
    "PartnerCreate",
    "PartnerUpdate",
    "PartnerOut",
    "PartnerPage",
]
