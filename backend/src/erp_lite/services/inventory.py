"""库存台账核心：库存流水写入 + 移动加权平均计价 + 现存量汇总。

金额与数量一律 Decimal(18,4) 运算，严禁 float 参与计算；
DB 列为 Numeric(18,4)，读取后统一 q4 归一（SQLite 下清除浮点痕迹）。
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.models import InventoryMove, Material, MoveType

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
    db: Session, material_id: int, warehouse_id: int | None = None
) -> tuple[Decimal, Decimal]:
    """现存量 / 现存金额 = 库存流水的 qty / amount 汇总（入库正、出库负）。"""
    stmt = select(
        func.coalesce(func.sum(InventoryMove.qty), 0),
        func.coalesce(func.sum(InventoryMove.amount), 0),
    ).where(InventoryMove.material_id == material_id)
    if warehouse_id is not None:
        stmt = stmt.where(InventoryMove.warehouse_id == warehouse_id)
    qty, amount = db.execute(stmt).one()
    return q4(qty), q4(amount)


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
) -> InventoryMove:
    """写一条库存流水并维护移动加权均价。

    - qty 带符号：入库正 / 出库负。
    - 入库：单价取传入 unit_cost，金额 = qty * unit_cost；
      新均价 = (现存金额 + 入库金额) / (现存数量 + 入库数量)（由台账汇总派生）。
    - 出库：单价取当前移动加权均价（现存量为 0 或库存不足时拒绝，报中文错误），
      金额 = qty * 均价（负数），unit_cost 记录出库成本。
    """
    db.flush()  # 同事务内先前写入的流水先落库，确保汇总包含它们
    qty_d = q4(qty)
    if qty_d == 0:
        raise ValueError("数量不能为 0")
    if not isinstance(move_type, MoveType):
        move_type = MoveType(move_type)

    stock_qty, stock_amt = get_stock(db, material_id, warehouse_id)
    if qty_d < 0:
        if stock_qty <= 0:
            raise ValueError(f"现存量为 0，禁止出库（物料 {material_id} / 仓库 {warehouse_id}）")
        if stock_qty + qty_d < 0:
            raise ValueError(
                f"库存不足：现存量 {stock_qty}，需出库 {-qty_d}"
                f"（物料 {material_id} / 仓库 {warehouse_id}）"
            )
        unit_cost_d = q4(stock_amt / stock_qty)  # 出库成本 = 当前移动加权均价
        amount = q4(qty_d * unit_cost_d)  # 负数
    else:
        unit_cost_d = q4(unit_cost)
        amount = q4(qty_d * unit_cost_d)

    move = InventoryMove(
        material_id=material_id,
        warehouse_id=warehouse_id,
        move_type=move_type,
        qty=qty_d,
        unit_cost=unit_cost_d,
        amount=amount,
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
    )
    db.add(move)
    db.flush()
    return move
