"""MRP 核心：BOM 递归展开、净需求计算（毛需求−现存量−在途）、环路检测。

金额与数量一律 Decimal 运算（复用 services.inventory.q4/_d），严禁 float。
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.models import (
    Bom,
    ManufacturingOrder,
    Material,
    MoveType,
    PurchaseOrder,
    PurchaseOrderLine,
)
from erp_lite.models.base import DocStatus
from erp_lite.services.batches import get_available
from erp_lite.services.inventory import _d, get_stock, q4

# 在途单据状态：已审核（approved）与部分执行（partial）都可能有未完成量
MO_IN_TRANSIT_STATUS = (DocStatus.approved, DocStatus.partial)
PO_IN_TRANSIT_STATUS = (DocStatus.approved, DocStatus.partial)


def get_active_bom(db: Session, material_id: int) -> Bom | None:
    """物料当前生效 BOM（v1 一物一 BOM）。"""
    return db.scalar(select(Bom).where(Bom.material_id == material_id))


def mo_completed_qty(db: Session, mo_id: int) -> Decimal:
    """MO 累计完工量 = mo_receipt 入库流水汇总。"""
    from erp_lite.models import InventoryMove

    total = db.scalar(
        select(func.sum(InventoryMove.qty)).where(
            InventoryMove.source_type == "manufacturing_order",
            InventoryMove.source_id == mo_id,
            InventoryMove.move_type == MoveType.mo_receipt,
        )
    )
    return q4(total or 0)


def po_in_transit(db: Session, material_id: int) -> Decimal:
    """在途采购量 = 已审核/部分入库 PO 的未收量(qty−received_qty)汇总。

    口径与 mo_in_transit 对齐：approved（全部未收）与 partial（剩余未收）都计入。
    """
    rows = db.execute(
        select(PurchaseOrderLine.qty, PurchaseOrderLine.received_qty)
        .join(PurchaseOrderLine.order)
        .where(
            PurchaseOrderLine.material_id == material_id,
            PurchaseOrderLine.order.has(PurchaseOrder.status.in_(PO_IN_TRANSIT_STATUS)),
        )
    ).all()
    return q4(sum((_d(q) - _d(r) for q, r in rows), Decimal("0")))


def mo_in_transit(db: Session, material_id: int) -> Decimal:
    """在途生产量 = 已下达 MO 未完工量汇总。"""
    rows = db.execute(
        select(ManufacturingOrder.id, ManufacturingOrder.planned_qty).where(
            ManufacturingOrder.material_id == material_id,
            ManufacturingOrder.status.in_(MO_IN_TRANSIT_STATUS),
        )
    ).all()
    return q4(
        sum((_d(planned) - mo_completed_qty(db, mo_id) for mo_id, planned in rows), Decimal("0"))
    )


def _cycle_error(path: tuple[int, ...], material_id: int) -> ValueError:
    chain = " → ".join(str(m) for m in (*path, material_id))
    return ValueError(f"BOM 存在环路，禁止保存/运算：{chain}")


def expand_requirements(
    db: Session,
    material_id: int,
    qty: Decimal | int | str,
    path: tuple[int, ...] = (),
) -> dict[int, Decimal]:
    """按 BOM 递归展开到叶子物料（无 BOM 者），返回 {material_id: 需求量}。

    path 为展开路径（物料 id 元组），路径中出现重复物料即成环，报中文错误。
    """
    qty_d = q4(qty)
    if material_id in path:
        raise _cycle_error(path, material_id)
    bom = get_active_bom(db, material_id)
    if bom is None:
        return {material_id: qty_d}
    out: dict[int, Decimal] = {}
    for line in bom.lines:
        child_qty = q4(qty_d * _d(line.qty_per) * (Decimal("1") + _d(line.loss_pct) / Decimal("100")))
        for mid, sub in expand_requirements(
            db, line.material_id, child_qty, (*path, material_id)
        ).items():
            out[mid] = q4(out.get(mid, Decimal("0")) + sub)
    return out


def _net_expand(
    db: Session,
    material_id: int,
    gross: Decimal,
    path: tuple[int, ...],
    acc: dict[tuple[int, str], Decimal],
) -> None:
    """净需求展开：有 BOM → make 并逐层向下，无 BOM → buy；净需求 ≤0 时截断。"""
    if material_id in path:
        raise _cycle_error(path, material_id)
    # v2-S3 可用量口径：批次物料只计 qualified 批次（pending/rejected/closed 不抵扣）
    material = db.get(Material, material_id)
    if material is not None and material.batch_required:
        stock_qty = get_available(db, material_id, only_qualified=True)
    else:
        stock_qty, _ = get_stock(db, material_id)
    transit = q4(po_in_transit(db, material_id) + mo_in_transit(db, material_id))
    net = q4(gross - stock_qty - transit)
    if net <= 0:
        return  # 库存与在途已覆盖，无需建议，也不再向下展开
    bom = get_active_bom(db, material_id)
    key = (material_id, "make" if bom is not None else "buy")
    acc[key] = q4(acc.get(key, Decimal("0")) + net)
    if bom is None:
        return
    for line in bom.lines:
        child_gross = q4(
            net * _d(line.qty_per) * (Decimal("1") + _d(line.loss_pct) / Decimal("100"))
        )
        _net_expand(db, line.material_id, child_gross, (*path, material_id), acc)


def run_mrp(db: Session, demands: list[tuple[int, Decimal | int | str]]) -> list[dict]:
    """MRP 运算：返回建议列表 [{material_id, type, net_qty}]（不落库，由调用方写 run/suggestions）。"""
    acc: dict[tuple[int, str], Decimal] = {}
    for material_id, qty in demands:
        if db.get(Material, material_id) is None:
            raise ValueError(f"物料不存在：{material_id}")
        _net_expand(db, material_id, q4(qty), (), acc)
    return [
        {"material_id": mid, "type": typ, "net_qty": net}
        for (mid, typ), net in acc.items()
    ]
