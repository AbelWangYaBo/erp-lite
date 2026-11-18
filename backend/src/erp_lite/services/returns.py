"""退货服务（v3 处置闭环）：采购退货 / 销售退货的真实账务联动。

口径（DESIGN-v3 §5.1/§5.2）：
- 采购退货（qa 处置 return 发起）：红字出库流水（带批次）→ 冲减未清应付（从早到晚，
  已核销部分不动并提示线下结算）→ 冲减 PO 行 received_qty（completed 回 partial 可重收）。
- 销售退货（sales 发起）：正数回流流水（批次回 pending 重检）→ 冲减未清应收 →
  冲减 SO 行 shipped_qty（completed 回 partial 可重发）。
- 全程与业务同事务，审计由调用方路由层写入。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import (
    Batch,
    DocStatus,
    InspStatus,
    InventoryMove,
    MoveType,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReturn,
    Receivable,
    SalesOrder,
    SalesOrderLine,
    SalesReturn,
    SettleStatus,
)
from erp_lite.models.finance import Payable
from erp_lite.services.inventory import get_avg_cost, get_batch_avg_cost, post_move, q4
from erp_lite.services.numbering import next_doc_no


def _apply_credit(
    bills: list,
    credit: Decimal,
) -> tuple[Decimal, str]:
    """把冲减额分摊到未清账单（从早到晚）：amount/settled_amount 同减，状态重算。

    返回 (实际冲减额, 说明)。已 settled 的账单不动；若冲减额超过未清总额，
    超出部分仅计入说明（需线下与往来单位结算）。
    """
    remaining = q4(credit)
    notes: list[str] = []
    for bill in bills:
        if remaining <= 0:
            break
        unsettled = q4(bill.amount) - q4(bill.settled_amount)
        if unsettled <= 0:
            continue
        cut = min(unsettled, remaining)
        bill.amount = q4(bill.amount - cut)
        bill.settled_amount = q4(min(bill.settled_amount, bill.amount))
        if bill.amount <= 0:
            bill.status = SettleStatus.settled
            bill.settled_amount = q4(0)
            bill.amount = q4(0)
        elif bill.settled_amount <= 0:
            bill.status = SettleStatus.open
        else:
            bill.status = SettleStatus.partial
        remaining = q4(remaining - cut)
    if remaining > 0:
        notes.append(
            f"未清金额不足，超出部分 {remaining} 已核销结清，需线下与往来单位结算"
        )
    applied = q4(credit - remaining)
    return applied, "；".join(notes)


def _line_of_batch(db: Session, batch_id: int, source_type: str, source_id: int):
    """经收发流水回溯来源行（退货冲减 received/shipped_qty 用）。"""
    move = db.scalar(
        select(InventoryMove)
        .where(
            InventoryMove.batch_id == batch_id,
            InventoryMove.source_type == source_type,
            InventoryMove.source_id == source_id,
            InventoryMove.source_line_id.isnot(None),
        )
        .order_by(InventoryMove.id.desc())
    )
    return move.source_line_id if move is not None else None


def create_purchase_return(
    db: Session,
    *,
    batch: Batch,
    po: PurchaseOrder,
    reason: str | None = None,
    actor: str | None = None,
) -> PurchaseReturn:
    """采购退货单（处置 return）：红字出库 + 冲应付 + 冲已收量，同事务。

    退货数量 = 批次当前现存（处置即全量退出库）；批次现存为 0 时 400
    （无货可退，应走报废销毁）。
    """
    from erp_lite.services.batches import get_batch_stock

    qty, _amount = get_batch_stock(db, batch.id)
    if qty <= 0:
        raise ValueError(
            f"批次 {batch.batch_no} 现存量为 0，无可退货数量，请选择报废销毁处置"
        )
    if batch.material_id and batch.source_type != "purchase_order":
        raise ValueError("仅采购来源批次支持退货处置；成品/半成品批次请走报废销毁")
    unit_price = get_batch_avg_cost(db, batch.id) or get_avg_cost(db, batch.material_id) or q4(0)
    ret_amount = q4(qty * unit_price)  # 冲减应付 = 现存 × 批次均价

    doc_no = next_doc_no(db, "PR")
    ret = PurchaseReturn(
        doc_no=doc_no,
        po_id=po.id,
        batch_id=batch.id,
        qty=qty,
        amount=ret_amount,
        reason=reason,
        created_by=actor,
    )
    db.add(ret)
    db.flush()  # 拿 id 供流水 source 引用

    post_move(
        db,
        material_id=batch.material_id,
        warehouse_id=_batch_warehouse(db, batch.id),
        move_type=MoveType.purchase_return,
        qty=-qty,
        source_type="purchase_return",
        source_id=ret.id,
        batch_id=batch.id,
    )

    aps = db.scalars(
        select(Payable)
        .where(Payable.source_type == "purchase_order", Payable.source_id == po.id)
        .order_by(Payable.id)
    ).all()
    applied, note = _apply_credit(aps, ret_amount)
    ret.amount = applied  # 实际冲减额（未清不足时缩小，剩余线下）

    line_id = _line_of_batch(db, batch.id, "purchase_order", po.id)
    if line_id is not None:
        line = db.get(PurchaseOrderLine, line_id)
        if line is not None:
            line.received_qty = q4(max(Decimal("0"), line.received_qty - qty))
    if po.status == DocStatus.completed:
        po.status = DocStatus.partial  # 允许重新收货补齐
    if note:
        ret.reason = f"{reason or ''}（{note}）" if reason else note
    return ret


def create_sales_return(
    db: Session,
    *,
    batch: Batch,
    so: SalesOrder,
    qty: Decimal,
    reason: str | None = None,
    actor: str | None = None,
) -> SalesReturn:
    """销售退货单：回流入库（批次回 pending 重检）+ 冲应收 + 冲已发量，同事务。

    退货数量不得超过该 SO 对该批次的累计出库量；单价按 SO 出库流水的出库成本
    近似（取批次现存均价，无则物料均价，再无则 0）。
    """
    shipped = q4(
        -sum(
            m.qty
            for m in db.scalars(
                select(InventoryMove).where(
                    InventoryMove.batch_id == batch.id,
                    InventoryMove.source_type == "sales_order",
                    InventoryMove.source_id == so.id,
                    InventoryMove.move_type == MoveType.sale_out,
                )
            ).all()
        )
    )
    if q4(qty) <= 0 or q4(qty) > shipped:
        raise ValueError(
            f"退货数量 {q4(qty)} 超出该订单对此批次的累计出库量 {shipped}"
        )
    returned = q4(
        sum(
            m.qty
            for m in db.scalars(
                select(InventoryMove).where(
                    InventoryMove.batch_id == batch.id,
                    InventoryMove.source_type == "sales_return",
                    InventoryMove.source_id.in_(
                        select(SalesReturn.id).where(SalesReturn.so_id == so.id)
                    ),
                )
            ).all()
        )
    )
    if shipped - returned <= 0:
        raise ValueError("该订单对此批次的出库量已全部退回")

    unit_price = get_batch_avg_cost(db, batch.id) or get_avg_cost(db, batch.material_id) or q4(0)
    ret_amount = q4(qty * unit_price)

    doc_no = next_doc_no(db, "SR")
    ret = SalesReturn(
        doc_no=doc_no,
        so_id=so.id,
        batch_id=batch.id,
        qty=q4(qty),
        amount=ret_amount,
        reason=reason,
        created_by=actor,
    )
    db.add(ret)
    db.flush()

    post_move(
        db,
        material_id=batch.material_id,
        warehouse_id=_batch_warehouse(db, batch.id),
        move_type=MoveType.sale_return,
        qty=qty,
        unit_cost=unit_price,
        source_type="sales_return",
        source_id=ret.id,
        batch_id=batch.id,
    )
    batch.insp_status = InspStatus.pending  # 退货品重检（GMP：放行后方可再出库）

    ars = db.scalars(
        select(Receivable)
        .where(Receivable.source_type == "sales_order", Receivable.source_id == so.id)
        .order_by(Receivable.id)
    ).all()
    applied, note = _apply_credit(ars, ret_amount)
    ret.amount = applied

    line_id = _line_of_batch(db, batch.id, "sales_order", so.id)
    if line_id is not None:
        line = db.get(SalesOrderLine, line_id)
        if line is not None:
            line.shipped_qty = q4(max(Decimal("0"), line.shipped_qty - qty))
    if so.status == DocStatus.completed:
        so.status = DocStatus.partial
    if note:
        ret.reason = f"{reason or ''}（{note}）" if reason else note
    return ret


def _batch_warehouse(db: Session, batch_id: int) -> int:
    """退货/报废流水的仓库归属：取该批次最近一条流水的仓库。"""
    move = db.scalar(
        select(InventoryMove)
        .where(InventoryMove.batch_id == batch_id)
        .order_by(InventoryMove.id.desc())
    )
    if move is None:
        raise ValueError(f"批次 #{batch_id} 无库存流水，无法确定退货仓库")
    return move.warehouse_id
