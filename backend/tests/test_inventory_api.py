"""库存台账测试：stock/moves 接口过滤、移动加权均价、Decimal 精度、采购→销售集成闭环。"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from conftest import grant_supplier_cert
from erp_lite.models import (
    InventoryMove,
    Material,
    MaterialCategory,
    MoveType,
    Partner,
    PartnerType,
    Warehouse,
)
from erp_lite.services.inventory import get_stock, post_move, q4, stock_by_material


def _seed(db_session):
    wh = Warehouse(code="W1", name="一号仓")
    wh2 = Warehouse(code="W2", name="二号仓")
    mat = Material(code="M1", name="铜锭", unit="kg", category=MaterialCategory.raw)
    mat2 = Material(code="M2", name="铝锭", unit="kg", category=MaterialCategory.raw)
    sup = Partner(code="SUP1", name="华东供应商", type=PartnerType.supplier)
    cus = Partner(code="CUS1", name="北方客户", type=PartnerType.customer)
    db_session.add_all([wh, wh2, mat, mat2, sup, cus])
    db_session.commit()
    grant_supplier_cert(db_session, sup.id)  # V2-S4 资质拦截适配
    return wh, wh2, mat, mat2, sup, cus


def _order(client, kind, partner_id, mat, wh, qty, price, date="2026-11-17"):
    prefix = "purchase-orders" if kind == "po" else "sales-orders"
    resp = client.post(
        f"/api/v1/{prefix}",
        json={
            "partner_id": partner_id,
            "order_date": date,
            "lines": [
                {"material_id": mat.id, "qty": qty, "unit_price": price, "warehouse_id": wh.id}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _act(client, kind, doc_id, action, body=None):
    prefix = "purchase-orders" if kind == "po" else "sales-orders"
    resp = client.post(f"/api/v1/{prefix}/{doc_id}/{action}", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_stock_endpoint_after_receive(client, db_session):
    wh, _, mat, _, sup, _ = _seed(db_session)
    po = _order(client, "po", sup.id, mat, wh, 100, 10)
    _act(client, "po", po["id"], "approve")
    _act(
        client, "po", po["id"], "receive",
        [{"line_id": po["lines"][0]["id"], "qty": 100, "warehouse_id": wh.id}],
    )
    resp = client.get("/api/v1/inventory/stock")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["material_id"] == mat.id
    assert float(row["qty"]) == 100
    assert float(row["amount"]) == 1000
    assert float(row["avg_cost"]) == 10


def test_stock_filters_by_warehouse_and_material(client, db_session):
    wh, wh2, mat, mat2, sup, _ = _seed(db_session)
    post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=10, unit_cost=Decimal("1"),
              source_type="test", source_id=1)
    post_move(db_session, material_id=mat.id, warehouse_id=wh2.id,
              move_type=MoveType.purchase_in, qty=20, unit_cost=Decimal("2"),
              source_type="test", source_id=2)
    post_move(db_session, material_id=mat2.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=30, unit_cost=Decimal("3"),
              source_type="test", source_id=3)
    db_session.commit()

    rows = client.get("/api/v1/inventory/stock", params={"warehouse_id": wh.id}).json()
    # W1 有 M1(10) 与 M2(30)，按物料汇总各一行
    assert len(rows) == 2
    by_mat = {r["material_id"]: r for r in rows}
    assert float(by_mat[mat.id]["qty"]) == 10
    assert float(by_mat[mat2.id]["qty"]) == 30

    rows = client.get("/api/v1/inventory/stock", params={"material_id": mat.id}).json()
    assert len(rows) == 1
    assert float(rows[0]["qty"]) == 30
    assert float(rows[0]["amount"]) == 50  # 10*1 + 20*2

    rows = client.get("/api/v1/inventory/stock").json()
    assert len(rows) == 2


def test_moves_endpoint_filters_and_desc_order(client, db_session):
    wh, _, mat, _, _, _ = _seed(db_session)
    for i in range(3):
        post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
                  move_type=MoveType.purchase_in, qty=1, unit_cost=Decimal("1"),
                  source_type="test", source_id=i + 1)
    db_session.commit()

    resp = client.get("/api/v1/inventory/moves", params={"material_id": mat.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    ids = [m["id"] for m in body["items"]]
    assert ids == sorted(ids, reverse=True)

    other_wh_move = client.get(
        "/api/v1/inventory/moves", params={"warehouse_id": 9999}
    ).json()
    assert other_wh_move["total"] == 0

    filtered = client.get(
        "/api/v1/inventory/moves",
        params={"material_id": mat.id, "from": "2026-11-17", "to": "2026-11-18"},
    ).json()
    assert filtered["total"] == 3

    paged = client.get(
        "/api/v1/inventory/moves", params={"page": 2, "page_size": 2}
    ).json()
    assert len(paged["items"]) == 1


def test_service_moving_average_and_decimal_precision(client, db_session):
    """0.1+0.2 类精度用例：入库 0.1@0.1 与 0.2@0.2，均价= 0.05/0.3 = 0.1667。"""
    wh, _, mat, _, _, _ = _seed(db_session)
    post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=Decimal("0.1"), unit_cost=Decimal("0.1"),
              source_type="test", source_id=1)
    post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=Decimal("0.2"), unit_cost=Decimal("0.2"),
              source_type="test", source_id=2)
    db_session.commit()
    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert qty == Decimal("0.3")
    assert amount == Decimal("0.05")
    assert q4(amount / qty) == Decimal("0.1667")

    # 出库 0.1：单价取当前均价 0.1667，出库金额 -0.0167
    post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
              move_type=MoveType.sale_out, qty=Decimal("-0.1"),
              source_type="test", source_id=3)
    db_session.commit()
    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert qty == Decimal("0.2")
    assert amount == Decimal("0.0333")
    move = db_session.scalar(select(InventoryMove).where(InventoryMove.move_type == MoveType.sale_out))
    assert Decimal(str(move.unit_cost)) == Decimal("0.1667")


def test_service_outbound_zero_stock_rejected(client, db_session):
    wh, _, mat, _, _, _ = _seed(db_session)
    with pytest.raises(ValueError) as exc:
        post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
                  move_type=MoveType.sale_out, qty=Decimal("-1"),
                  source_type="test", source_id=1)
    assert "现存量为 0" in str(exc.value)


def test_service_stock_by_material_grouping(client, db_session):
    wh, wh2, mat, mat2, _, _ = _seed(db_session)
    post_move(db_session, material_id=mat.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=5, unit_cost=Decimal("2"),
              source_type="test", source_id=1)
    post_move(db_session, material_id=mat.id, warehouse_id=wh2.id,
              move_type=MoveType.purchase_in, qty=7, unit_cost=Decimal("2"),
              source_type="test", source_id=2)
    post_move(db_session, material_id=mat2.id, warehouse_id=wh.id,
              move_type=MoveType.purchase_in, qty=1, unit_cost=Decimal("9"),
              source_type="test", source_id=3)
    db_session.commit()
    rows = {r.material_id: r for r in stock_by_material(db_session)}
    assert rows[mat.id].qty == Decimal("12")
    assert rows[mat.id].amount == Decimal("24")
    assert rows[mat2.id].qty == Decimal("1")
    rows_wh1 = {r.material_id: r for r in stock_by_material(db_session, warehouse_id=wh.id)}
    assert rows_wh1[mat.id].qty == Decimal("5")


def test_integration_purchase_to_sale_full_loop(client, db_session):
    """完整闭环：采购入库（分两次）→ 销售出库，全程库存数字核对。"""
    wh, _, mat, _, sup, cus = _seed(db_session)
    # 采购 100 @10，分两次入库 40 + 60
    po = _order(client, "po", sup.id, mat, wh, 100, 10)
    _act(client, "po", po["id"], "approve")
    _act(client, "po", po["id"], "receive",
         [{"line_id": po["lines"][0]["id"], "qty": 40, "warehouse_id": wh.id}])
    _act(client, "po", po["id"], "receive",
         [{"line_id": po["lines"][0]["id"], "qty": 60, "warehouse_id": wh.id}])

    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert (qty, amount) == (Decimal("100"), Decimal("1000"))

    # 销售 30 @15，全部出库
    so = _order(client, "so", cus.id, mat, wh, 30, 15)
    _act(client, "so", so["id"], "approve")
    _act(client, "so", so["id"], "ship",
         [{"line_id": so["lines"][0]["id"], "qty": 30, "warehouse_id": wh.id}])

    qty, amount = get_stock(db_session, mat.id, wh.id)
    assert (qty, amount) == (Decimal("70"), Decimal("700"))

    stock = client.get("/api/v1/inventory/stock").json()[0]
    assert float(stock["qty"]) == 70
    assert float(stock["avg_cost"]) == 10
    assert float(stock["amount"]) == 700

    moves = client.get("/api/v1/inventory/moves").json()["items"]
    types = sorted(m["move_type"] for m in moves)
    assert types == ["purchase_in", "purchase_in", "sale_out"]
