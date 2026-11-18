"""角色工作台（v3）：按当前角色返回待办计数与直达目标。

DESIGN-v3 §5.7：待办驱动替代平铺列表——每个角色登录即看到「轮到我做什么」。
计数口径：
- purchase：待审 PO（draft 且非本人创建——不得自审，本人创建的显示提示不计入）
- sales：待审 SO（draft）；warehouse：待收 PO 行（approved/partial）、可出库 SO（approved/partial）
- production：待下达 MO（draft）、待完工 MO（approved/partial）
- qc：待检批次（pending 且无未判定记录）；qa：待判定批次（pending 且已有未判定记录）、
  待审资质（draft）、待处置批次（rejected）
- finance：待核销 AP/AR（open/partial）、待过账盘点（draft）
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import (
    Batch,
    DocStatus,
    InspStatus,
    InspectionRecord,
    ManufacturingOrder,
    PartnerCert,
    Payable,
    PurchaseOrder,
    Receivable,
    SalesOrder,
    SettleStatus,
    StockCount,
    User,
)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"], dependencies=[Depends(get_current_user)])


class TodoItem(BaseModel):
    key: str
    label: str
    count: int
    target: str  # 前端路由 path


def _pending_judge_batch_ids(db: Session) -> set[int]:
    """有未判定检验记录的批次 id 集合（qa 待判定 / qc 待检的分界）。"""
    rows = db.scalars(
        select(InspectionRecord.batch_id).where(InspectionRecord.conclusion.is_(None))
    ).all()
    return set(rows)


@router.get("")
def my_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    role = current_user.role
    todos: list[TodoItem] = []
    judge_ids = _pending_judge_batch_ids(db)

    if role in ("purchase", "admin"):
        # 待审 PO：draft 且非本人创建（admin 也不自审，规则 A）
        n = db.scalar(
            select(func.count()).select_from(PurchaseOrder).where(
                PurchaseOrder.status == DocStatus.draft,
                PurchaseOrder.created_by != current_user.username,
            )
        ) or 0
        mine = db.scalar(
            select(func.count()).select_from(PurchaseOrder).where(
                PurchaseOrder.status == DocStatus.draft,
                PurchaseOrder.created_by == current_user.username,
            )
        ) or 0
        todos.append(TodoItem(key="po_to_approve", label="待我审核的采购订单", count=n, target="/purchase-orders"))
        todos.append(TodoItem(key="po_my_draft", label="我创建的 PO 草稿（待他人审核）", count=mine, target="/purchase-orders"))

    if role in ("sales", "admin"):
        n = db.scalar(
            select(func.count()).select_from(SalesOrder).where(
                SalesOrder.status == DocStatus.draft,
                SalesOrder.created_by != current_user.username,
            )
        ) or 0
        todos.append(TodoItem(key="so_to_approve", label="待我审核的销售订单", count=n, target="/sales-orders"))

    if role in ("warehouse", "admin"):
        n = db.scalar(
            select(func.count()).select_from(PurchaseOrder).where(
                PurchaseOrder.status.in_((DocStatus.approved, DocStatus.partial))
            )
        ) or 0
        todos.append(TodoItem(key="po_to_receive", label="待收货采购订单", count=n, target="/purchase-orders"))
        n = db.scalar(
            select(func.count()).select_from(SalesOrder).where(
                SalesOrder.status.in_((DocStatus.approved, DocStatus.partial))
            )
        ) or 0
        todos.append(TodoItem(key="so_to_ship", label="待出库销售订单", count=n, target="/sales-orders"))

    if role in ("production", "admin"):
        n = db.scalar(
            select(func.count()).select_from(ManufacturingOrder).where(
                ManufacturingOrder.status == DocStatus.draft,
                ManufacturingOrder.created_by != current_user.username,
            )
        ) or 0
        todos.append(TodoItem(key="mo_to_release", label="待我下达的生产工单", count=n, target="/manufacturing-orders"))
        n = db.scalar(
            select(func.count()).select_from(ManufacturingOrder).where(
                ManufacturingOrder.status.in_((DocStatus.approved, DocStatus.partial))
            )
        ) or 0
        todos.append(TodoItem(key="mo_in_progress", label="进行中工单（待领料/完工）", count=n, target="/manufacturing-orders"))

    if role in ("qc", "admin"):
        n = db.scalar(
            select(func.count()).select_from(Batch).where(
                Batch.insp_status == InspStatus.pending,
                Batch.id.not_in(judge_ids) if judge_ids else Batch.id.isnot(None),
            )
        ) or 0
        todos.append(TodoItem(key="batch_to_inspect", label="待录入检验记录批次", count=n, target="/inspections"))

    if role in ("qa", "admin"):
        if judge_ids:
            n = db.scalar(
                select(func.count()).select_from(Batch).where(
                    Batch.insp_status == InspStatus.pending,
                    Batch.id.in_(judge_ids),
                )
            ) or 0
        else:
            n = 0
        todos.append(TodoItem(key="batch_to_judge", label="待复核判定批次", count=n, target="/inspections"))
        n = db.scalar(
            select(func.count()).select_from(Batch).where(
                Batch.insp_status == InspStatus.rejected
            )
        ) or 0
        todos.append(TodoItem(key="batch_to_dispose", label="待处置不合格批次", count=n, target="/inspections"))
        n = db.scalar(
            select(func.count()).select_from(PartnerCert).where(
                PartnerCert.audit_status == "draft"
            )
        ) or 0
        todos.append(TodoItem(key="cert_to_audit", label="待审核供应商资质", count=n, target="/partner-certs"))

    if role in ("finance", "admin"):
        n = db.scalar(
            select(func.count()).select_from(Payable).where(
                Payable.status.in_((SettleStatus.open, SettleStatus.partial))
            )
        ) or 0
        todos.append(TodoItem(key="ap_to_settle", label="待核销应付", count=n, target="/payables"))
        n = db.scalar(
            select(func.count()).select_from(Receivable).where(
                Receivable.status.in_((SettleStatus.open, SettleStatus.partial))
            )
        ) or 0
        todos.append(TodoItem(key="ar_to_settle", label="待核销应收", count=n, target="/receivables"))
        n = db.scalar(
            select(func.count()).select_from(StockCount).where(StockCount.status == "draft")
        ) or 0
        todos.append(TodoItem(key="sc_to_post", label="待过账盘点单", count=n, target="/stock-counts"))

    return {"role": role, "todos": [t.model_dump() for t in todos]}
