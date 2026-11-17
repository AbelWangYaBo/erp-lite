"""全部 SQLAlchemy 模型的汇总入口（供 alembic autogenerate 与业务导入）。"""

from erp_lite.models.base import (
    Base,
    DocMixin,
    DocStatus,
    InspStatus,
    MaterialCategory,
    MoveType,
    PartnerType,
    SettleStatus,
    TimestampMixin,
)
from erp_lite.models.users import Role, User
from erp_lite.models.master_data import Material, Partner, Warehouse
from erp_lite.models.batch import Batch
from erp_lite.models.purchase import PurchaseOrder, PurchaseOrderLine
from erp_lite.models.sales import SalesOrder, SalesOrderLine
from erp_lite.models.inventory import InventoryMove
from erp_lite.models.production import (
    Bom,
    BomLine,
    ManufacturingOrder,
    MoIssue,
    MrpRun,
    MrpSuggestion,
)
from erp_lite.models.finance import Payable, Receivable, Settlement, SettlementLine
from erp_lite.models.audit import AuditRecord, DocSequence, OperationLog

__all__ = [
    "Base",
    "DocMixin",
    "TimestampMixin",
    "DocStatus",
    "InspStatus",
    "MaterialCategory",
    "PartnerType",
    "MoveType",
    "SettleStatus",
    "User",
    "Role",
    "Warehouse",
    "Material",
    "Partner",
    "Batch",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "SalesOrder",
    "SalesOrderLine",
    "InventoryMove",
    "Bom",
    "BomLine",
    "MrpRun",
    "MrpSuggestion",
    "ManufacturingOrder",
    "MoIssue",
    "Payable",
    "Receivable",
    "Settlement",
    "SettlementLine",
    "AuditRecord",
    "OperationLog",
    "DocSequence",
]
