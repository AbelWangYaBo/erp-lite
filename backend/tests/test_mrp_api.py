"""MRP 测试：递归展开（手算净需求）/ 无 BOM 采购 / 环路 400 / 建议转 PO、MO 草稿（幂等 400）。"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select

from erp_lite.models import (
    Bom,
    BomLine,
    ManufacturingOrder,
    Material,
    MaterialCategory,
    Partner,
    PartnerType,
    PurchaseOrder,
    MrpSuggestion,
    Warehouse,
)
from conftest import grant_supplier_cert
from erp_lite.services.inventory import post_move


def _seed(db_session, with_bom=True):
    """FG(BOM: RAW×2) / RAW；FG 现存 30、在途 20（已审核 PO 未收）。"""
    wh = Warehouse(code="W1", name="一号仓")
    fg = Material(code="FG", name="成品", unit="个", category=MaterialCategory.finished,
                  default_warehouse_id=None)
    raw = Material(code="RAW", name="原料", unit="kg", category=MaterialCategory.raw)
    sup = Partner(code="SUP1", name="供应商", type=PartnerType.supplier)
    db_session.add_all([wh, fg, raw, sup])
    db_session.commit()
    if with_bom:
        bom = Bom(material_id=fg.id)
        db_session.add(bom)
        db_session.flush()
        db_session.add(BomLine(bom_id=bom.id, material_id=raw.id,
                               qty_per=Decimal("2"), loss_pct=Decimal("0")))
        db_session.commit()
    # FG 现存 30（调整入库，成本 0 简化）
    post_move(db_session, material_id=fg.id, warehouse_id=wh.id, move_type="adjust",
              qty=Decimal("30"), unit_cost=Decimal("0"),
              source_type="adjust", source_id=0)
    db_session.commit()
    return wh, fg, raw, sup


def _mrp_run(client, fg_id, qty=100):
    return client.post(
        "/api/v1/mrp/run",
        json={"demand": [{"material_id": fg_id, "qty": qty}]},
    )


def test_mrp_expansion_net_requirement_hand_calc(client, db_session):
    """手算核对：FG 毛需求100，现存30，在途20 → 净需求50(make)；子件 RAW 毛需求=50×2=100，现存0 → 净需求100(buy)。"""
    wh, fg, raw, sup = _seed(db_session)
    # 在途：已审核 PO 未收 20
    from erp_lite.models import PurchaseOrderLine

    po = PurchaseOrder(doc_no="PO20261117001", status="approved", partner_id=sup.id,
                       order_date=date(2026, 11, 17))
    db_session.add(po)
    db_session.flush()
    db_session.add(PurchaseOrderLine(po_id=po.id, material_id=fg.id,
                                     qty=Decimal("20"), unit_price=Decimal("5"),
                                     received_qty=Decimal("0"), warehouse_id=wh.id))
    db_session.commit()

    resp = _mrp_run(client, fg.id)
    assert resp.status_code == 200, resp.text
    sugs = {s["type"]: s for s in resp.json()["suggestions"]}
    assert Decimal(str(sugs["make"]["net_qty"])) == Decimal("50")
    assert sugs["make"]["material_id"] == fg.id
    assert Decimal(str(sugs["buy"]["net_qty"])) == Decimal("100")
    assert sugs["buy"]["material_id"] == raw.id


def test_mrp_material_without_bom_is_buy(client, db_session):
    wh, fg, raw, sup = _seed(db_session, with_bom=False)
    resp = client.post(
        "/api/v1/mrp/run",
        json={"demand": [{"material_id": raw.id, "qty": 100}]},
    )
    assert resp.status_code == 200
    sugs = resp.json()["suggestions"]
    assert len(sugs) == 1
    assert sugs[0]["type"] == "buy"
    # 现存 0（raw 无库存），净需求=100
    assert Decimal(str(sugs[0]["net_qty"])) == Decimal("100")


def test_mrp_run_persists_run_and_log(client, db_session):
    wh, fg, raw, sup = _seed(db_session)
    resp = _mrp_run(client, fg.id)
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id > 0
    logs = client.get("/api/v1/audit/logs").json()
    assert any(l["action"] == "mrp_run" for l in logs["items"])


def test_mrp_cycle_400(client, db_session):
    wh, fg, raw, sup = _seed(db_session)
    # RAW 也建 BOM 且子件为 FG：FG→RAW→FG 成环
    bom = Bom(material_id=raw.id)
    db_session.add(bom)
    db_session.flush()
    db_session.add(BomLine(bom_id=bom.id, material_id=fg.id,
                           qty_per=Decimal("1"), loss_pct=Decimal("0")))
    db_session.commit()
    resp = _mrp_run(client, fg.id)
    assert resp.status_code == 400
    assert "环" in resp.json()["detail"]


def test_mrp_partial_po_in_transit_deducted(client, db_session):
    """回归：partial PO 未收量也须计入在途。PO 100 收 40 后 → 在途 60、现存 40。

    demand RAW 200 → 净需求 = 200 − 现存 40 − 在途 60 = 100
    （修复前漏算 partial PO 在途 → 净需求 160）。
    """
    from erp_lite.models import DocStatus, PurchaseOrderLine

    wh, fg, raw, sup = _seed(db_session, with_bom=False)
    po = PurchaseOrder(doc_no="PO20261117001", status=DocStatus.approved,
                       partner_id=sup.id, order_date=date(2026, 11, 17))
    db_session.add(po)
    db_session.flush()
    line = PurchaseOrderLine(po_id=po.id, material_id=raw.id,
                             qty=Decimal("100"), unit_price=Decimal("5"),
                             received_qty=Decimal("0"), warehouse_id=wh.id)
    db_session.add(line)
    db_session.commit()

    # 入库 40 → PO 转 partial，未收 60
    resp = client.post(
        f"/api/v1/purchase-orders/{po.id}/receive",
        json=[{"line_id": line.id, "qty": 40, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "partial"

    resp = client.post(
        "/api/v1/mrp/run",
        json={"demand": [{"material_id": raw.id, "qty": 200}]},
    )
    assert resp.status_code == 200, resp.text
    sugs = resp.json()["suggestions"]
    assert len(sugs) == 1
    assert sugs[0]["type"] == "buy"
    assert Decimal(str(sugs[0]["net_qty"])) == Decimal("100")


def test_convert_buy_creates_po_draft_and_idempotent(client, db_session):
    wh, fg, raw, sup = _seed(db_session, with_bom=False)
    raw.default_warehouse_id = wh.id
    db_session.commit()
    resp = _mrp_run(client, raw.id)
    sug_id = resp.json()["suggestions"][0]["id"]
    grant_supplier_cert(db_session, sup.id)  # v3：MRP 转 PO 前校验供应商资质
    resp = client.post(
        "/api/v1/mrp/suggestions/convert",
        json={"ids": [sug_id], "partner_id": sup.id},
    )
    assert resp.status_code == 200, resp.text
    po = db_session.scalar(select(PurchaseOrder))
    assert po is not None and po.status == "draft"
    assert po.doc_no.startswith("PO")
    assert po.lines[0].material_id == raw.id
    sug = db_session.get(MrpSuggestion, sug_id)
    assert sug.converted is True
    assert sug.converted_doc_id == po.id
    # 幂等：已转换的再转 → 400
    resp = client.post(
        "/api/v1/mrp/suggestions/convert",
        json={"ids": [sug_id], "partner_id": sup.id},
    )
    assert resp.status_code == 400
    assert "转换" in resp.json()["detail"] or "已" in resp.json()["detail"]


def test_convert_buy_without_partner_400(client, db_session):
    wh, fg, raw, sup = _seed(db_session, with_bom=False)
    resp = _mrp_run(client, raw.id)
    sug_id = resp.json()["suggestions"][0]["id"]
    resp = client.post("/api/v1/mrp/suggestions/convert", json={"ids": [sug_id]})
    assert resp.status_code == 400


def test_convert_make_creates_mo_draft(client, db_session):
    wh, fg, raw, sup = _seed(db_session)
    fg.default_warehouse_id = wh.id
    db_session.commit()
    resp = _mrp_run(client, fg.id)
    make_id = [s for s in resp.json()["suggestions"] if s["type"] == "make"][0]["id"]
    resp = client.post("/api/v1/mrp/suggestions/convert", json={"ids": [make_id]})
    assert resp.status_code == 200, resp.text
    mo = db_session.scalar(select(ManufacturingOrder))
    assert mo is not None and mo.status == "draft"
    assert mo.material_id == fg.id
    assert Decimal(str(mo.planned_qty)) == Decimal("70")  # 净需求=毛需求100−现存30=70
    assert mo.doc_no.startswith("MO")


def test_suggestions_list_filter_and_pagination(client, db_session):
    wh, fg, raw, sup = _seed(db_session)
    resp = _mrp_run(client, fg.id)
    assert resp.status_code == 200
    resp = client.get("/api/v1/mrp/suggestions", params={"converted": "false"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    # 分页
    resp = client.get(
        "/api/v1/mrp/suggestions", params={"converted": "false", "page": 1, "page_size": 1}
    )
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
