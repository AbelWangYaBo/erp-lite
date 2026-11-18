"""生产工单 MO 测试：建单/下达/领料（BOM 展开+指定行+库存不足）/完工入库成本手算/关闭。"""

from decimal import Decimal

from sqlalchemy import select

from erp_lite.models import (
    Bom,
    BomLine,
    InventoryMove,
    ManufacturingOrder,
    Material,
    MaterialCategory,
    Warehouse,
)
from conftest import switch_user
from erp_lite.services.inventory import get_stock, post_move


def _seed(db_session, stock_qty=Decimal("0"), stock_price=Decimal("10")):
    """RAW 原料（可入库）+ FG 成品（BOM: RAW×2）。"""
    wh = Warehouse(code="W1", name="一号仓")
    fg = Material(code="FG", name="成品", unit="个", category=MaterialCategory.finished)
    raw = Material(code="RAW", name="原料", unit="kg", category=MaterialCategory.raw)
    db_session.add_all([wh, fg, raw])
    db_session.commit()
    bom = Bom(material_id=fg.id)
    db_session.add(bom)
    db_session.flush()
    db_session.add(BomLine(bom_id=bom.id, material_id=raw.id,
                           qty_per=Decimal("2"), loss_pct=Decimal("0")))
    db_session.commit()
    if stock_qty > 0:
        # 原料入库（adjust 简化，均价=stock_price）
        post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
                  move_type="adjust", qty=stock_qty, unit_cost=stock_price,
                  source_type="adjust", source_id=0)
        db_session.commit()
    return wh, fg, raw


def _create_mo(client, db_session, planned=10):
    wh, fg, raw = _seed(db_session)
    resp = client.post(
        "/api/v1/manufacturing-orders",
        json={"material_id": fg.id, "planned_qty": planned, "warehouse_id": wh.id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json(), wh, fg, raw


def _approve_mo(client, mo_id):
    switch_user(client, "approver")  # v3：不得自审，MO 下达换 approver
    resp = client.post(f"/api/v1/manufacturing-orders/{mo_id}/approve")
    assert resp.status_code == 200, resp.text
    switch_user(client, "tester")  # 切回创建人身份


def test_create_mo_draft_and_approve(client, db_session):
    body, wh, fg, raw = _create_mo(client, db_session)
    assert body["status"] == "draft"
    assert body["doc_no"].startswith("MO")
    assert body["doc_no"].endswith("001")
    _approve_mo(client, body["id"])
    resp = client.get(f"/api/v1/manufacturing-orders/{body['id']}")
    assert resp.json()["status"] == "approved"
    # 状态留痕
    recs = client.get(
        "/api/v1/audit/records",
        params={"doc_type": "manufacturing_order", "doc_id": body["id"]},
    ).json()
    actions = [r["action"] for r in recs["items"]]
    assert "create" in actions and "status" in actions


def test_issue_by_bom_expansion_and_cost(client, db_session):
    """计划 10 件，整单领料按 BOM 展开：RAW 需 20，均价 10 → 领料成本 200。"""
    body, wh, fg, raw = _create_mo(client, db_session)
    _seed_stock = post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
                            move_type="adjust", qty=Decimal("100"),
                            unit_cost=Decimal("10"), source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue", json={})
    assert resp.status_code == 200, resp.text
    mo = db_session.get(ManufacturingOrder, body["id"])
    assert Decimal(str(mo.issued_qty_cost)) == Decimal("200")
    issue = db_session.scalar(select(InventoryMove).where(InventoryMove.move_type == "mo_issue"))
    assert Decimal(str(issue.qty)) == Decimal("-20")
    assert Decimal(str(issue.unit_cost)) == Decimal("10")
    qty, amount = get_stock(db_session, raw.id, wh.id)
    assert qty == Decimal("80")


def test_issue_insufficient_stock_400(client, db_session):
    body, wh, fg, raw = _create_mo(client, db_session)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("10"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue", json={})
    assert resp.status_code == 400
    assert "库存不足" in resp.json()["detail"]


def test_issue_with_explicit_lines(client, db_session):
    """无 BOM 物料用 body 指定行领料。"""
    wh = Warehouse(code="W1", name="一号仓")
    fg = Material(code="FG", name="无BOM成品", unit="个", category=MaterialCategory.finished)
    raw = Material(code="PACK", name="包材", unit="个", category=MaterialCategory.raw)
    db_session.add_all([wh, fg, raw])
    db_session.commit()
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("50"), unit_cost=Decimal("1"),
              source_type="adjust", source_id=0)
    db_session.commit()
    resp = client.post(
        "/api/v1/manufacturing-orders",
        json={"material_id": fg.id, "planned_qty": 10, "warehouse_id": wh.id},
    )
    assert resp.status_code == 200, resp.text
    mo_id = resp.json()["id"]
    _approve_mo(client, mo_id)  # v3：不得自审，MO 下达换 approver
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo_id}/issue",
        json={"lines": [{"material_id": raw.id, "qty": 5}]},
    )
    assert resp.status_code == 200, resp.text
    mo = db_session.get(ManufacturingOrder, mo_id)
    assert Decimal(str(mo.issued_qty_cost)) == Decimal("5")


def test_receive_full_completion_cost_hand_calc(client, db_session):
    """手算核对：领料成本 1000 + extra 100，完工全部 10 件 → 入库单价 (1000×1+100)/10=110，金额 1100。"""
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("500"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    # 领料 100 件 RAW @10 = 1000（BOM×2 超出计划，仅用于凑成本 1000）
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/issue",
        json={"lines": [{"material_id": raw.id, "qty": 100}]},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/receive",
        json={"qty": 10, "extra_cost": 100},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    mo = db_session.get(ManufacturingOrder, body["id"])
    assert Decimal(str(mo.issued_qty_cost)) == Decimal("1000")
    assert Decimal(str(mo.extra_cost)) == Decimal("100")
    qty, amount = get_stock(db_session, fg.id, wh.id)
    assert qty == Decimal("10")
    assert amount == Decimal("1100")  # 入库单价 110 × 10
    move = db_session.scalar(
        select(InventoryMove).where(InventoryMove.move_type == "mo_receipt")
    )
    assert Decimal(str(move.unit_cost)) == Decimal("110")
    assert Decimal(str(move.amount)) == Decimal("1100")


def test_receive_partial_then_completed(client, db_session):
    """部分完工 → partial；按比例结转：领料 200，完工 5/10 → (200×0.5+0)/5=20/件。"""
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("100"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue", json={})  # 领 20 RAW = 200
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/receive",
        json={"qty": 5, "extra_cost": 0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "partial"
    qty, amount = get_stock(db_session, fg.id, wh.id)
    assert qty == Decimal("5")
    assert amount == Decimal("100")  # 单价 20 × 5
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/receive",
        json={"qty": 5, "extra_cost": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_receive_over_completion_400(client, db_session):
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("100"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue", json={})
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/receive",
        json={"qty": 11, "extra_cost": 0},
    )
    assert resp.status_code == 400
    assert "完工" in resp.json()["detail"] or "超过" in resp.json()["detail"]


def test_close_mo(client, db_session):
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    _approve_mo(client, body["id"])
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/close")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "closed"


def test_receive_interleaved_issue_fg_amount_equals_total_cost(client, db_session):
    """回归：交错领料完工不漏结转。领 10 RAW@10（成本100）→ 完工5 → 再领 10 RAW@10（累计200）
    → 完工5 → 关闭。成品库存金额须 = 累计领料 200 + extra 0 = 200，数量仍为 10。"""
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("100"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue",
                       json={"lines": [{"material_id": raw.id, "qty": 10}]})
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/receive",
                       json={"qty": 5, "extra_cost": 0})
    assert resp.status_code == 200, resp.text
    # 再领 10 RAW，均价仍 10 → 累计领料成本 200
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue",
                       json={"lines": [{"material_id": raw.id, "qty": 10}]})
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/receive",
                       json={"qty": 5, "extra_cost": 0})
    assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/close")
    assert resp.status_code == 200, resp.text
    qty, amount = get_stock(db_session, fg.id, wh.id)
    assert qty == Decimal("10")  # adjust 只调金额不改数量
    assert amount == Decimal("200")  # = 累计领料 200 + extra 0


def test_close_transfers_remaining_cost_no_residue(client, db_session):
    """回归：close 把剩余未结转成本以 adjust 流水调增成品金额，transferred_cost 无残留。"""
    body, wh, fg, raw = _create_mo(client, db_session, planned=10)
    post_move(db_session, material_id=raw.id, warehouse_id=wh.id,
              move_type="adjust", qty=Decimal("100"), unit_cost=Decimal("10"),
              source_type="adjust", source_id=0)
    db_session.commit()
    _approve_mo(client, body["id"])
    client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue",
                json={"lines": [{"material_id": raw.id, "qty": 10}]})  # 成本 100
    client.post(f"/api/v1/manufacturing-orders/{body['id']}/receive",
                json={"qty": 5, "extra_cost": 0})  # 按比例结转 50，单价 10
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/close")
    assert resp.status_code == 200, resp.text
    mo = db_session.get(ManufacturingOrder, body["id"])
    assert Decimal(str(mo.issued_qty_cost)) == Decimal("100")
    assert Decimal(str(mo.transferred_cost)) == Decimal("100")  # 无残留
    adjust = db_session.scalar(
        select(InventoryMove).where(
            InventoryMove.move_type == "adjust",
            InventoryMove.source_type == "manufacturing_order",
            InventoryMove.source_id == body["id"],
        )
    )
    assert adjust is not None
    assert Decimal(str(adjust.amount)) == Decimal("50")  # 剩余 100-50
    assert Decimal(str(adjust.qty)) == Decimal("0")
    qty, amount = get_stock(db_session, fg.id, wh.id)
    assert qty == Decimal("5")
    assert amount == Decimal("100")  # 50 完工入库 + 50 关闭调增


def test_issue_with_nonexistent_material_400(client, db_session):
    """回归：领料 lines 中物料不存在 → 400 中文错误（引用校验缺口）。"""
    body, wh, fg, raw = _create_mo(client, db_session)
    _approve_mo(client, body["id"])
    resp = client.post(
        f"/api/v1/manufacturing-orders/{body['id']}/issue",
        json={"lines": [{"material_id": 99999, "qty": 5}]},
    )
    assert resp.status_code == 400
    assert "物料不存在" in resp.json()["detail"]


def test_issue_before_approve_400(client, db_session):
    body, wh, fg, raw = _create_mo(client, db_session)
    resp = client.post(f"/api/v1/manufacturing-orders/{body['id']}/issue", json={})
    assert resp.status_code == 400
