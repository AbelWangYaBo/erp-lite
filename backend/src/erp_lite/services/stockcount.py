"""盘点服务（v3）：账面快照建单 → 录实盘 → post 生成带批次 adjust 流水。

口径（DESIGN-v3 §5.4）：
- 建单（warehouse）：按仓库现存自动快照全部 (material, batch) 组合为行，system_qty 即账面；
  批次物料按批次盘（batch_id 必有），非批次物料 batch_id=None。
- post（finance/admin）：diff = counted - system，非零行逐条写 adjust 流水
  （unit_cost 取当前批次级/物料级均价），盘盈入库盘亏出库，账实差异可修正。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import Batch, InspStatus, InventoryMove, StockCount, StockCountLine
from erp_lite.services.inventory import get_batch_avg_cost, get_avg_cost, post_move, q4


def snapshot_warehouse(db: Session, warehouse_id: int) -> list[StockCountLine]:
    """快照仓库账面：有流水的 (material_id, batch_id) 组合逐条建行，qty 为账面量。"""
    rows = (
        db.execute(
            select(
                InventoryMove.material_id,
                InventoryMove.batch_id,
                InventoryMove.qty,
            ).where(InventoryMove.warehouse_id == warehouse_id)
        )
        .all()
    )
    agg: dict[tuple[int, int | None], Decimal] = {}
    for material_id, batch_id, qty in rows:
        key = (material_id, batch_id)
        agg[key] = agg.get(key, Decimal("0")) + q4(qty)
    lines = [
        StockCountLine(
            material_id=material_id,
            batch_id=batch_id,
            system_qty=q4(qty),
        )
        for (material_id, batch_id), qty in sorted(agg.items())
        if qty != 0  # 零现存组合不盘（历史冲平的行无意义）
    ]
    return lines


def post_stock_count(
    db: Session, count: StockCount, *, posted_by: str
) -> tuple[int, Decimal]:
    """过账：非零差异行写 adjust 流水；返回 (差异行数, 盘盈盈亏金额合计绝对值)。

    批次物料过账成本取批次级均价（无则物料级）；非批次物料取物料级均价。
    盘亏出库由 post_move 负数路径自动计成本；盘盈入库按同价计入。
    """
    diff_lines = 0
    moved_amount = Decimal("0")
    for line in count.lines:
        if line.counted_qty is None:
            raise ValueError(f"盘点单 {count.doc_no} 存在未录实盘的行（物料 #{line.material_id}）")
        diff = q4(line.counted_qty) - q4(line.system_qty)
        line.diff_qty = diff
        if diff == 0:
            continue
        batch = db.get(Batch, line.batch_id) if line.batch_id is not None else None
        if batch is not None and batch.insp_status not in (InspStatus.pending, InspStatus.qualified):
            # rejected/closed 批次账面不应再有可盘差异（处置已清库存）；仍允许 adjust 修正
            pass
        if line.batch_id is not None:
            unit_cost = get_batch_avg_cost(db, line.batch_id) or get_avg_cost(
                db, line.material_id, count.warehouse_id
            )
        else:
            unit_cost = get_avg_cost(db, line.material_id, count.warehouse_id)
        if unit_cost is None:
            unit_cost = Decimal("0")
        move = post_move(
            db,
            material_id=line.material_id,
            warehouse_id=count.warehouse_id,
            move_type="adjust",
            qty=diff,
            unit_cost=unit_cost,
            source_type="stock_count",
            source_id=count.id,
            source_line_id=line.id,
            batch_id=line.batch_id,
        )
        diff_lines += 1
        moved_amount = q4(moved_amount + abs(move.amount))
    count.status = "posted"
    count.posted_by = posted_by
    return diff_lines, moved_amount
