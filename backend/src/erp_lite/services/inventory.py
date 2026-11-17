"""库存台账核心：库存流水写入 + 移动加权平均计价 + 现存量汇总。

金额与数量一律 Decimal(18,4) 运算，严禁 float 参与计算；
DB 列为 Numeric(18,4)，读取后统一 q4 归一（SQLite 下清除浮点痕迹）。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.models import Batch, InventoryMove, Material, MoveType

QUANT = Decimal("0.0001")


class StockRow(NamedTuple):
    material_id: int
    qty: Decimal
    amount: Decimal


def _d(value) -> Decimal:
    """任意数值安全转 Decimal：先转 str，避免 float 二进制误差。"""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def q4(value) -> Decimal:
    """统一四舍五入到 4 位小数。"""
    return _d(value).quantize(QUANT, rounding=ROUND_HALF_UP)


def get_stock(
    db: Session,
    material_id: int,
    warehouse_id: int | None = None,
    batch_id: int | None = None,
) -> tuple[Decimal, Decimal]:
    """现存量 / 现存金额 = 库存流水的 qty / amount 汇总（入库正、出库负）。

    batch_id 传入时按 (material_id, batch_id) 批次维度汇总（v2 批次级库存）；
    不传则保持 v1 物料级口径。
    """
    stmt = select(
        func.coalesce(func.sum(InventoryMove.qty), 0),
        func.coalesce(func.sum(InventoryMove.amount), 0),
    ).where(InventoryMove.material_id == material_id)
    if warehouse_id is not None:
        stmt = stmt.where(InventoryMove.warehouse_id == warehouse_id)
    if batch_id is not None:
        stmt = stmt.where(InventoryMove.batch_id == batch_id)
    qty, amount = db.execute(stmt).one()
    return q4(qty), q4(amount)


def get_batch_avg_cost(db: Session, batch_id: int) -> Decimal | None:
    """批次级移动加权均价 = 批次现存金额 / 批次现存量；无量可比时返回 None。"""
    stmt = select(
        func.coalesce(func.sum(InventoryMove.qty), 0),
        func.coalesce(func.sum(InventoryMove.amount), 0),
    ).where(InventoryMove.batch_id == batch_id)
    qty, amount = db.execute(stmt).one()
    qty, amount = q4(qty), q4(amount)
    if qty <= 0:
        return None
    return q4(amount / qty)


def get_avg_cost(
    db: Session, material_id: int, warehouse_id: int | None = None
) -> Decimal | None:
    """当前移动加权均价 = 现存金额 / 现存量；无量可比时返回 None。"""
    qty, amount = get_stock(db, material_id, warehouse_id)
    if qty <= 0:
        return None
    return q4(amount / qty)


def stock_by_material(
    db: Session,
    material_id: int | None = None,
    warehouse_id: int | None = None,
):
    """按物料汇总现存量/现存金额（可按仓库过滤），返回 (material_id, qty, amount) 行。"""
    stmt = select(
        InventoryMove.material_id,
        func.coalesce(func.sum(InventoryMove.qty), 0).label("qty"),
        func.coalesce(func.sum(InventoryMove.amount), 0).label("amount"),
    ).group_by(InventoryMove.material_id)
    if material_id is not None:
        stmt = stmt.where(InventoryMove.material_id == material_id)
    if warehouse_id is not None:
        stmt = stmt.where(InventoryMove.warehouse_id == warehouse_id)
    rows = db.execute(stmt).all()
    return [StockRow(r[0], q4(r[1]), q4(r[2])) for r in rows]


def post_move(
    db: Session,
    *,
    material_id: int,
    warehouse_id: int,
    move_type: MoveType | str,
    qty: Decimal | int | float | str,
    unit_cost: Decimal | int | float | str = 0,
    source_type: str,
    source_id: int,
    source_line_id: int | None = None,
    amount: Decimal | int | float | str | None = None,
    batch_id: int | None = None,
) -> InventoryMove:
    """写一条库存流水并维护移动加权平均计价。

    - qty 带符号：入库正 / 出库负。
    - batch_id（v2 批次物料）：须指向存在且属于该物料的批次；
      出库时按批次维度校验现存并取批次级均价计成本。非批次物料传 None，
      完全走 v1 物料级路径。
    - 入库：单价取传入 unit_cost，金额 = qty * unit_cost；
      新均价 = (现存金额 + 入库金额) / (现存数量 + 入库数量)（由台账汇总派生）。
    - 出库：单价取当前移动加权均价（现存量为 0 或库存不足时拒绝，报中文错误），
      金额 = qty * 均价（负数），unit_cost 记录出库成本。
    - adjust 允许 qty=0 的纯金额调整（须显式传 amount）：只调库存金额不改数量，
      用于 MO 关闭时剩余成本调增；unit_cost 仅作单价留痕。
    - 并发：开头对物料行 SELECT ... FOR UPDATE。PostgreSQL 下该行锁把同一物料的
      出入库 check-then-act 串行化，防止并发事务基于同一现存快照错算均价/超卖；
      SQLite 忽略 FOR UPDATE（测试内存库单连接串行，无并发）。
    """
    db.flush()  # 同事务内先前写入的流水先落库，确保汇总包含它们
    qty_d = q4(qty)
    if not isinstance(move_type, MoveType):
        move_type = MoveType(move_type)
    # 物料行锁（PG 下生效，见 docstring）
    db.execute(select(Material).where(Material.id == material_id).with_for_update())
    # 批次校验（v2）：批次须存在且属于该物料；出库按批次维度计量
    if batch_id is not None:
        batch = db.get(Batch, batch_id)
        if batch is None:
            raise ValueError(f"批次不存在：{batch_id}")
        if batch.material_id != material_id:
            raise ValueError(
                f"批次 {batch.batch_no} 不属于物料 {material_id}，禁止混用"
            )
    if qty_d == 0:
        if move_type is not MoveType.adjust or amount is None:
            raise ValueError("数量不能为 0")
        unit_cost_d = q4(unit_cost)
        amount_d = q4(amount)
    elif qty_d < 0:
        stock_qty, stock_amt = get_stock(db, material_id, warehouse_id, batch_id=batch_id)
        scope = (
            f"批次 {batch.batch_no}"
            if batch_id is not None
            else f"物料 {material_id} / 仓库 {warehouse_id}"
        )
        if stock_qty <= 0:
            raise ValueError(f"现存量为 0，禁止出库（{scope}）")
        if stock_qty + qty_d < 0:
            raise ValueError(
                f"库存不足：现存量 {stock_qty}，需出库 {-qty_d}（{scope}）"
            )
        unit_cost_d = q4(stock_amt / stock_qty)  # 出库成本 = 当前移动加权均价
        amount_d = q4(qty_d * unit_cost_d)  # 负数
    else:
        unit_cost_d = q4(unit_cost)
        amount_d = q4(qty_d * unit_cost_d)

    move = InventoryMove(
        material_id=material_id,
        warehouse_id=warehouse_id,
        move_type=move_type,
        qty=qty_d,
        unit_cost=unit_cost_d,
        amount=amount_d,
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
        batch_id=batch_id,
    )
    db.add(move)
    db.flush()
    return move
