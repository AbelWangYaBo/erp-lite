"""审查修复回归：行锁钉住（并发语义 SQLite 无法复现，以源码断言钉住 with_for_update）。"""

import inspect

from erp_lite.routers import finance, purchase, sales
from erp_lite.services import inventory


def test_post_move_locks_material_row():
    """post_move 须对物料行 SELECT ... FOR UPDATE，串行化同一物料的出入库。"""
    src = inspect.getsource(inventory.post_move)
    assert "with_for_update" in src


def test_purchase_receive_locks_po_row():
    """采购入库须对 PO 行加锁，防止并发入库超收。"""
    src = inspect.getsource(purchase.receive_purchase_order)
    assert "_get_or_404_for_update" in src
    assert "with_for_update" in inspect.getsource(purchase._get_or_404_for_update)


def test_sales_ship_locks_so_row():
    """销售出库须对 SO 行加锁，防止并发出库超发/超卖。"""
    src = inspect.getsource(sales.ship_sales_order)
    assert "_get_or_404_for_update" in src
    assert "with_for_update" in inspect.getsource(sales._get_or_404_for_update)


def test_create_settlement_locks_target_row():
    """核销须对 Payable/Receivable 目标行加锁后再校验与累加 settled_amount。"""
    src = inspect.getsource(finance.create_settlement)
    assert "with_for_update" in src
