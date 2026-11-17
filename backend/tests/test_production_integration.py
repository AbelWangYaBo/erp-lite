"""生产主线集成用例：采购原料 → 建 BOM → MRP → 转 MO → 领料 → 完工 → 成品库存与成本核对。"""

from decimal import Decimal

from sqlalchemy import select

from conftest import grant_supplier_cert
from erp_lite.models import (
    ManufacturingOrder,
    Material,
    MaterialCategory,
    Partner,
    PartnerType,
    Warehouse,
)
from erp_lite.services.inventory import get_stock


def test_full_production_loop(client, db_session):
    # 1) 基础数据：一号仓 / 原料 RAW / 成品 FG / 供应商
    wh = Warehouse(code="W1", name="一号仓")
    db_session.add(wh)
    db_session.commit()
    raw = Material(code="RAW", name="铜锭", unit="kg", category=MaterialCategory.raw,
                   default_warehouse_id=wh.id)
    fg = Material(code="FG", name="成品轴", unit="个", category=MaterialCategory.finished,
                  default_warehouse_id=wh.id)
    sup = Partner(code="SUP1", name="供应商甲", type=PartnerType.supplier)
    db_session.add_all([raw, fg, sup])
    db_session.commit()
    grant_supplier_cert(db_session, sup.id)  # V2-S4 资质拦截：供应商默认资质合规

    # 2) 采购 100kg 原料 @10，审核+入库 → 现存 100，金额 1000，均价 10
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [{"material_id": raw.id, "qty": 100, "unit_price": 10,
                       "warehouse_id": wh.id}],
        },
    )
    assert resp.status_code == 200, resp.text
    po = resp.json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": 100, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    qty, amount = get_stock(db_session, raw.id, wh.id)
    assert qty == Decimal("100") and amount == Decimal("1000")

    # 3) 建 BOM：1 件 FG = 2kg RAW（损耗 0）
    resp = client.put(
        f"/api/v1/boms/{fg.id}",
        json={"lines": [{"material_id": raw.id, "qty_per": 2, "loss_pct": 0}]},
    )
    assert resp.status_code == 200, resp.text

    # 4) MRP：需求 FG 30 件，现存 0、在途 0 → make 30；RAW 毛需求 60，现存 100 → 净需求 0（不生成 buy）
    resp = client.post(
        "/api/v1/mrp/run", json={"demand": [{"material_id": fg.id, "qty": 30}]}
    )
    assert resp.status_code == 200, resp.text
    sugs = resp.json()["suggestions"]
    assert len(sugs) == 1 and sugs[0]["type"] == "make"
    assert Decimal(str(sugs[0]["net_qty"])) == Decimal("30")

    # 5) 转 MO 草稿 → 下达
    resp = client.post("/api/v1/mrp/suggestions/convert",
                       json={"ids": [sugs[0]["id"]]})
    assert resp.status_code == 200, resp.text
    mo = db_session.scalar(select(ManufacturingOrder))
    assert mo.material_id == fg.id
    assert Decimal(str(mo.planned_qty)) == Decimal("30")
    # MO 建在物料默认仓/转换仓 → 指定 W1
    resp = client.get(f"/api/v1/manufacturing-orders/{mo.id}")
    assert resp.json()["status"] == "draft"
    client.post(f"/api/v1/manufacturing-orders/{mo.id}/approve")

    # 6) 领料（整单按 BOM）：60kg RAW @10 = 600 → 原料现存 40
    resp = client.post(f"/api/v1/manufacturing-orders/{mo.id}/issue", json={})
    assert resp.status_code == 200, resp.text
    qty, amount = get_stock(db_session, raw.id, wh.id)
    assert qty == Decimal("40") and amount == Decimal("400")

    # 7) 完工 30 件，extra_cost=60 → 入库单价 (600×1+60)/30 = 22，金额 660
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/receive",
        json={"qty": 30, "extra_cost": 60},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"
    qty, amount = get_stock(db_session, fg.id, wh.id)
    assert qty == Decimal("30")
    assert amount == Decimal("660")  # 单价 22 × 30
    # 原料不变
    qty, amount = get_stock(db_session, raw.id, wh.id)
    assert qty == Decimal("40")

    # 8) 关闭工单
    resp = client.post(f"/api/v1/manufacturing-orders/{mo.id}/close")
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
