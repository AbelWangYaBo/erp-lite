"""批次内核测试（V2-S2）：批次台账/批次级库存与均价/UDI 字段/出入库接批次。

铁律：非批次物料走 v1 原路径（现有用例不改断言，全绿即回归证明）。
"""

from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.main import app
from erp_lite.models import Batch, InventoryMove, Material, MaterialCategory
from erp_lite.models.base import InspStatus
from erp_lite.models.master_data import Partner, PartnerType, Warehouse
from erp_lite.services.batches import get_available, get_batch_stock
from erp_lite.services.inventory import get_avg_cost, get_stock


def _mk_wh(db, code="W1"):
    wh = db.scalar(select(Warehouse).where(Warehouse.code == code))
    if wh is not None:
        return wh
    wh = Warehouse(code=code, name=f"仓库{code}")
    db.add(wh)
    return wh


def _mk_mat(db, code, batch_required=False, category=MaterialCategory.raw, **kw):
    mat = Material(
        code=code,
        name=f"物料{code}",
        unit="kg",
        category=category,
        batch_required=batch_required,
        **kw,
    )
    db.add(mat)
    return mat


def _mk_partner(db, code, ptype):
    p = Partner(code=code, name=f"单位{code}", type=ptype)
    db.add(p)
    return p


def _mk_batch_material_po_received(client, db, qty, price, batch_no, mat_code="RB"):
    """建批次物料 + 供应商 + 采购入库一个批次，返回 (mat, sup, po, batch)。"""
    wh = _mk_wh(db)
    mat = _mk_mat(db, mat_code, batch_required=True)
    sup = _mk_partner(db, f"S{mat_code}", PartnerType.supplier)
    db.commit()
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {
                    "material_id": mat.id,
                    "qty": qty,
                    "unit_price": price,
                    "warehouse_id": wh.id,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    po = resp.json()
    resp = client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[
            {
                "line_id": po["lines"][0]["id"],
                "qty": qty,
                "warehouse_id": wh.id,
                "batch_no": batch_no,
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    assert batch is not None
    return mat, sup, po, batch


def _inspect_pass(client, batch_id):
    """V2-S3：QA 判定合格放行（pending 批次领料/出库被拦截，先判合格再用）。"""
    resp = client.post(
        f"/api/v1/batches/{batch_id}/inspect",
        json={
            "items": [{"name": "外观", "result": "合格"}],
            "conclusion": "pass",
        },
    )
    assert resp.status_code == 200, resp.text


# ---------- 采购入库接批次 ----------


def test_purchase_receive_creates_batch_ledger(client, db_session):
    """批次物料入库生成批次台账：来源=PO、供应商、pending、流水落 batch_id。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-A"
    )
    assert batch.material_id == mat.id
    assert batch.source_type == "purchase_order"
    assert batch.source_id == po["id"]
    assert batch.supplier_id == sup.id
    assert batch.insp_status == InspStatus.pending
    move = db_session.scalar(
        select(InventoryMove).where(InventoryMove.batch_id == batch.id)
    )
    assert move is not None
    assert float(move.qty) == 10


def test_receive_batch_required_without_batch_no_400(client, db_session):
    """批次物料入库不传 batch_no → 400 中文错误。"""
    wh = _mk_wh(db_session)
    mat = _mk_mat(db_session, "RB1", batch_required=True)
    sup = _mk_partner(db_session, "SRB1", PartnerType.supplier)
    db_session.commit()
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 5, "unit_price": 1, "warehouse_id": wh.id}
            ],
        },
    )
    po = resp.json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": 5, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400
    assert "批次" in resp.json()["detail"]


def test_receive_reuses_existing_batch_no(client, db_session):
    """同批次号再次入库复用同一批次记录，现存累加、台账只有一条。"""
    mat, sup, po1, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-R"
    )
    wh = db_session.scalar(select(Warehouse))
    # 第二张 PO 再次入库同批次号
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {
                    "material_id": mat.id,
                    "qty": 5,
                    "unit_price": 4,
                    "warehouse_id": wh.id,
                }
            ],
        },
    )
    po2 = resp.json()
    client.post(f"/api/v1/purchase-orders/{po2['id']}/approve")
    resp = client.post(
        f"/api/v1/purchase-orders/{po2['id']}/receive",
        json=[
            {
                "line_id": po2["lines"][0]["id"],
                "qty": 5,
                "warehouse_id": wh.id,
                "batch_no": "LOT-R",
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    batches = db_session.scalars(select(Batch).where(Batch.batch_no == "LOT-R")).all()
    assert len(batches) == 1
    qty, amount = get_batch_stock(db_session, batch.id)
    assert qty == Decimal("15")
    # 移动加权：(10*2 + 5*4) / 15 = 40/15
    assert amount == Decimal("40")


def test_receive_batch_no_belongs_to_other_material_400(client, db_session):
    """批次号已属于其他物料 → 400，不串物料。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-X"
    )
    wh = db_session.scalar(select(Warehouse))
    other = _mk_mat(db_session, "RB2", batch_required=True)
    sup2 = _mk_partner(db_session, "SRB2", PartnerType.supplier)
    db_session.commit()
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup2.id,
            "order_date": "2026-11-17",
            "lines": [
                {
                    "material_id": other.id,
                    "qty": 5,
                    "unit_price": 1,
                    "warehouse_id": wh.id,
                }
            ],
        },
    )
    po2 = resp.json()
    client.post(f"/api/v1/purchase-orders/{po2['id']}/approve")
    resp = client.post(
        f"/api/v1/purchase-orders/{po2['id']}/receive",
        json=[
            {
                "line_id": po2["lines"][0]["id"],
                "qty": 5,
                "warehouse_id": wh.id,
                "batch_no": "LOT-X",
            }
        ],
    )
    assert resp.status_code == 400
    assert "物料" in resp.json()["detail"]


# ---------- 批次级现存 / 均价（手算核对） ----------


def test_multi_batch_independent_stock_and_avg_cost(client, db_session):
    """两批次独立现存/均价手算核对，物料级均价与批次级并存。"""
    wh = _mk_wh(db_session)
    mat = _mk_mat(db_session, "MC1", batch_required=True)
    sup = _mk_partner(db_session, "SMC1", PartnerType.supplier)
    db_session.commit()
    batch_ids = {}
    for batch_no, qty, price in (("LOT-A", 10, "2"), ("LOT-B", 10, "4")):
        resp = client.post(
            "/api/v1/purchase-orders",
            json={
                "partner_id": sup.id,
                "order_date": "2026-11-17",
                "lines": [
                    {
                        "material_id": mat.id,
                        "qty": qty,
                        "unit_price": price,
                        "warehouse_id": wh.id,
                    }
                ],
            },
        )
        po = resp.json()
        client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
        resp = client.post(
            f"/api/v1/purchase-orders/{po['id']}/receive",
            json=[
                {
                    "line_id": po["lines"][0]["id"],
                    "qty": qty,
                    "warehouse_id": wh.id,
                    "batch_no": batch_no,
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        batch_ids[batch_no] = db_session.scalar(
            select(Batch.id).where(Batch.batch_no == batch_no)
        )

    # 批次级：A = 10 × 2 = 20，均价 2；B = 10 × 4 = 40，均价 4
    qty_a, amt_a = get_batch_stock(db_session, batch_ids["LOT-A"])
    qty_b, amt_b = get_batch_stock(db_session, batch_ids["LOT-B"])
    assert (qty_a, amt_a) == (Decimal("10"), Decimal("20"))
    assert (qty_b, amt_b) == (Decimal("10"), Decimal("40"))
    # 物料级（v1 口径并存）：20 / 60，均价 3
    qty, amt = get_stock(db_session, mat.id)
    assert (qty, amt) == (Decimal("20"), Decimal("60"))
    assert get_avg_cost(db_session, mat.id) == Decimal("3")


def test_batch_avg_cost_after_outbound_hand_calc(client, db_session):
    """出库后批次均价不变（批次内移动加权）、物料级均价重算。"""
    mat, sup, po, batch_a = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-A"
    )
    wh = db_session.scalar(select(Warehouse))
    # 第二批 10 @ 4
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 10, "unit_price": 4, "warehouse_id": wh.id}
            ],
        },
    )
    po2 = resp.json()
    client.post(f"/api/v1/purchase-orders/{po2['id']}/approve")
    client.post(
        f"/api/v1/purchase-orders/{po2['id']}/receive",
        json=[
            {
                "line_id": po2["lines"][0]["id"],
                "qty": 10,
                "warehouse_id": wh.id,
                "batch_no": "LOT-B",
            }
        ],
    )
    batch_b = db_session.scalar(select(Batch).where(Batch.batch_no == "LOT-B"))
    # QA 合格放行 LOT-A（V2-S3 起仅 qualified 批次可出库）
    _inspect_pass(client, batch_a.id)
    # SO 出库 6 个，从 LOT-A
    cus = _mk_partner(db_session, "CA", PartnerType.customer)
    db_session.commit()
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": cus.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 6, "unit_price": 10, "warehouse_id": wh.id}
            ],
        },
    )
    so = resp.json()
    client.post(f"/api/v1/sales-orders/{so['id']}/approve")
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[
            {
                "line_id": so["lines"][0]["id"],
                "qty": 6,
                "warehouse_id": wh.id,
                "batch_id": batch_a.id,
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    # 手算：LOT-A 出库成本 = 批次均价 2，出库后 4 个 / 8 元，均价仍 2
    qty_a, amt_a = get_batch_stock(db_session, batch_a.id)
    assert (qty_a, amt_a) == (Decimal("4"), Decimal("8"))
    # LOT-B 不受影响
    qty_b, amt_b = get_batch_stock(db_session, batch_b.id)
    assert (qty_b, amt_b) == (Decimal("10"), Decimal("40"))
    # 物料级：(8+40)/(4+10) = 48/14
    qty, amt = get_stock(db_session, mat.id)
    assert qty == Decimal("14")
    assert amt == Decimal("48")


# ---------- 非批次物料回归（v1 原路径） ----------


def test_non_batch_material_v1_path_no_batch(client, db_session):
    """非批次物料入库/出库：不建批次、流水 batch_id=None、物料级均价照旧。"""
    wh = _mk_wh(db_session)
    mat = _mk_mat(db_session, "NB1", batch_required=False)
    sup = _mk_partner(db_session, "SNB1", PartnerType.supplier)
    cus = _mk_partner(db_session, "CNB1", PartnerType.customer)
    db_session.commit()
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 10, "unit_price": 3, "warehouse_id": wh.id}
            ],
        },
    )
    po = resp.json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    # 不传 batch_no（v1 请求体原样）
    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": 10, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    assert db_session.scalars(select(Batch)).first() is None
    move = db_session.scalar(select(InventoryMove))
    assert move.batch_id is None
    assert get_avg_cost(db_session, mat.id) == Decimal("3")
    # SO 出库不传 batch_id 也照旧
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": cus.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 4, "unit_price": 8, "warehouse_id": wh.id}
            ],
        },
    )
    so = resp.json()
    client.post(f"/api/v1/sales-orders/{so['id']}/approve")
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[{"line_id": so["lines"][0]["id"], "qty": 4, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    qty, _ = get_stock(db_session, mat.id)
    assert qty == Decimal("6")


# ---------- SO 出库 / MO 领料接批次 ----------


def _mk_so_shipped(client, db, batch):
    """建客户 + SO + 审核出库指定批次。"""
    wh = db.scalar(select(Warehouse))
    mat = db.get(Material, batch.material_id)
    cus = _mk_partner(db, "CSO", PartnerType.customer)
    db.commit()
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": cus.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 3, "unit_price": 9, "warehouse_id": wh.id}
            ],
        },
    )
    so = resp.json()
    client.post(f"/api/v1/sales-orders/{so['id']}/approve")
    return so, wh, cus


def test_ship_batch_required_without_batch_id_400(client, db_session):
    """批次物料出库不传 batch_id → 400 中文错误。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-S"
    )
    so, wh, cus = _mk_so_shipped(client, db_session, batch)
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[{"line_id": so["lines"][0]["id"], "qty": 3, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 400
    assert "批次" in resp.json()["detail"]


def test_ship_with_batch_deducts_batch_stock(client, db_session):
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-D"
    )
    _inspect_pass(client, batch.id)  # QA 合格放行后方可出库
    so, wh, cus = _mk_so_shipped(client, db_session, batch)
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[
            {
                "line_id": so["lines"][0]["id"],
                "qty": 3,
                "warehouse_id": wh.id,
                "batch_id": batch.id,
            }
        ],
    )
    assert resp.status_code == 200, resp.text
    qty, _ = get_batch_stock(db_session, batch.id)
    assert qty == Decimal("7")


def test_ship_batch_of_other_material_400(client, db_session):
    """出库指定不属于该物料的批次 → 400。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-M"
    )
    # 另一物料 LOT2 也入库
    _mk_batch_material_po_received(client, db_session, 10, "2", "LOT2", mat_code="RB9")
    other = db_session.scalar(select(Batch).where(Batch.batch_no == "LOT2"))
    so, wh, cus = _mk_so_shipped(client, db_session, batch)
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[
            {
                "line_id": so["lines"][0]["id"],
                "qty": 3,
                "warehouse_id": wh.id,
                "batch_id": other.id,
            }
        ],
    )
    assert resp.status_code == 400
    assert "物料" in resp.json()["detail"]


def _mk_mo_for(db, product, wh):
    from erp_lite.models import ManufacturingOrder
    from erp_lite.services.numbering import next_doc_no

    mo = ManufacturingOrder(
        doc_no=next_doc_no(db, "MO"),
        material_id=product.id,
        planned_qty=Decimal("10"),
        warehouse_id=wh.id,
    )
    db.add(mo)
    db.commit()
    db.refresh(mo)
    return mo


def _approve_mo(client, mo_id):
    resp = client.post(f"/api/v1/manufacturing-orders/{mo_id}/approve")
    assert resp.status_code == 200, resp.text


def test_issue_batch_required_without_batch_id_400(client, db_session):
    """批次物料领料不传 batch_id → 400 中文错误。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-I"
    )
    wh = db_session.scalar(select(Warehouse))
    product = _mk_mat(db_session, "PD1", category=MaterialCategory.finished)
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/issue",
        json={"lines": [{"material_id": mat.id, "qty": 4}]},
    )
    assert resp.status_code == 400
    assert "批次" in resp.json()["detail"]


def test_issue_with_batch_deducts_batch_stock(client, db_session):
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-J"
    )
    _inspect_pass(client, batch.id)  # QA 合格放行后方可领料
    wh = db_session.scalar(select(Warehouse))
    product = _mk_mat(db_session, "PD2", category=MaterialCategory.finished)
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/issue",
        json={
            "lines": [{"material_id": mat.id, "qty": 4, "batch_id": batch.id}],
        },
    )
    assert resp.status_code == 200, resp.text
    qty, _ = get_batch_stock(db_session, batch.id)
    assert qty == Decimal("6")


def test_issue_batch_of_other_material_400(client, db_session):
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "LOT-K"
    )
    _mk_batch_material_po_received(client, db_session, 10, "2", "LOT9", mat_code="RB8")
    other = db_session.scalar(select(Batch).where(Batch.batch_no == "LOT9"))
    wh = db_session.scalar(select(Warehouse))
    product = _mk_mat(db_session, "PD3", category=MaterialCategory.finished)
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/issue",
        json={
            "lines": [{"material_id": mat.id, "qty": 4, "batch_id": other.id}],
        },
    )
    assert resp.status_code == 400


# ---------- MO 完工入库自动生成成品批次 ----------


def test_mo_receive_creates_qualified_finished_batch(client, db_session):
    """完工入库自动生成成品批次：batch_no=MO单号-序号、qualified、supplier 为空。"""
    wh = _mk_wh(db_session)
    product = _mk_mat(db_session, "FP1", batch_required=True, category=MaterialCategory.finished)
    db_session.commit()
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/receive",
        json={"qty": 10, "extra_cost": 0},
    )
    assert resp.status_code == 200, resp.text
    batch = db_session.scalar(
        select(Batch).where(Batch.material_id == product.id)
    )
    assert batch is not None
    assert batch.batch_no == f"{mo.doc_no}-1"
    # S2 阶段直接建为 qualified（S3 接 QA 检验判定），注释见 services/batches.py
    assert batch.insp_status == InspStatus.qualified
    assert batch.source_type == "manufacturing_order"
    assert batch.source_id == mo.id
    assert batch.supplier_id is None


def test_mo_receive_multiple_receipts_seq_increments(client, db_session):
    """多次完工入库生成 MO单号-1、MO单号-2 两个批次。"""
    wh = _mk_wh(db_session)
    product = _mk_mat(db_session, "FP2", batch_required=True, category=MaterialCategory.finished)
    db_session.commit()
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    for qty in (4, 6):
        resp = client.post(
            f"/api/v1/manufacturing-orders/{mo.id}/receive",
            json={"qty": qty, "extra_cost": 0},
        )
        assert resp.status_code == 200, resp.text
    nos = sorted(
        b.batch_no
        for b in db_session.scalars(select(Batch).where(Batch.material_id == product.id))
    )
    assert nos == [f"{mo.doc_no}-1", f"{mo.doc_no}-2"]


def test_mo_receive_non_batch_product_no_batch(client, db_session):
    """非批次成品完工入库不建批次（v1 回归）。"""
    wh = _mk_wh(db_session)
    product = _mk_mat(db_session, "FP3", batch_required=False, category=MaterialCategory.finished)
    db_session.commit()
    mo = _mk_mo_for(db_session, product, wh)
    _approve_mo(client, mo.id)
    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo.id}/receive",
        json={"qty": 5, "extra_cost": 0},
    )
    assert resp.status_code == 200, resp.text
    assert db_session.scalars(select(Batch)).first() is None


# ---------- 可用量（qualified 过滤开关） ----------


def test_get_available_qualified_filter_switch(client, db_session):
    """get_available：默认含 pending（S2 口径），开关打开只算 qualified。"""
    wh = _mk_wh(db_session)
    mat = _mk_mat(db_session, "AV1", batch_required=True)
    sup = _mk_partner(db_session, "SAV1", PartnerType.supplier)
    db_session.commit()
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": sup.id,
            "order_date": "2026-11-17",
            "lines": [
                {"material_id": mat.id, "qty": 10, "unit_price": 1, "warehouse_id": wh.id}
            ],
        },
    )
    po = resp.json()
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[
            {
                "line_id": po["lines"][0]["id"],
                "qty": 10,
                "warehouse_id": wh.id,
                "batch_no": "LOT-P",
            }
        ],
    )
    batch = db_session.scalar(select(Batch).where(Batch.batch_no == "LOT-P"))
    # 入库批次为 pending
    assert batch.insp_status == InspStatus.pending
    # S2 口径：默认含 pending
    assert get_available(db_session, mat.id) == Decimal("10")
    # 开关打开：qualified 过滤生效，pending 不计入
    assert get_available(db_session, mat.id, only_qualified=True) == Decimal("0")
    batch.insp_status = InspStatus.qualified
    db_session.commit()
    assert get_available(db_session, mat.id, only_qualified=True) == Decimal("10")


# ---------- 批次查询 API ----------


def test_batches_list_pagination_and_filters(client, db_session):
    """GET /api/v1/batches：分页 + material_id / insp_status / keyword 过滤。"""
    wh = _mk_wh(db_session)
    m1 = _mk_mat(db_session, "LB1", batch_required=True)
    m2 = _mk_mat(db_session, "LB2", batch_required=True)
    sup = _mk_partner(db_session, "SLB", PartnerType.supplier)
    db_session.commit()
    for mat, no in ((m1, "FILTER-1"), (m2, "FILTER-2")):
        resp = client.post(
            "/api/v1/purchase-orders",
            json={
                "partner_id": sup.id,
                "order_date": "2026-11-17",
                "lines": [
                    {
                        "material_id": mat.id,
                        "qty": 10,
                        "unit_price": 1,
                        "warehouse_id": wh.id,
                    }
                ],
            },
        )
        po = resp.json()
        client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
        client.post(
            f"/api/v1/purchase-orders/{po['id']}/receive",
            json=[
                {
                    "line_id": po["lines"][0]["id"],
                    "qty": 10,
                    "warehouse_id": wh.id,
                    "batch_no": no,
                }
            ],
        )

    resp = client.get("/api/v1/batches", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1

    resp = client.get("/api/v1/batches", params={"material_id": m2.id})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_no"] == "FILTER-2"

    resp = client.get("/api/v1/batches", params={"insp_status": "pending"})
    assert all(item["insp_status"] == "pending" for item in resp.json()["items"])

    resp = client.get("/api/v1/batches", params={"keyword": "FILTER-1"})
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["batch_no"] == "FILTER-1"


def test_batch_detail_stock_avg_insp_status(client, db_session):
    """GET /api/v1/batches/{id}：含现存量/均价/检验状态。"""
    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2.5", "DET-1"
    )
    resp = client.get(f"/api/v1/batches/{batch.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["batch_no"] == "DET-1"
    assert body["insp_status"] == "pending"
    assert float(body["stock_qty"]) == 10
    assert float(body["stock_amount"]) == 25
    assert float(body["avg_cost"]) == 2.5

    resp = client.get("/api/v1/batches/999999")
    assert resp.status_code == 404


def test_warehouse_role_can_query_batches(client, db_session):
    """仓库角色可查批次台账（batch.trace），本阶段无 inspect.judge 也不影响查询。"""
    from erp_lite.services.rbac import actions_for

    assert "batch.trace" in actions_for("warehouse")
    assert "inspect.judge" not in actions_for("warehouse")

    mat, sup, po, batch = _mk_batch_material_po_received(
        client, db_session, 10, "2", "ROLE-1"
    )
    fake_wh_user = SimpleNamespace(id=2, username="wh_user", role="warehouse")
    app.dependency_overrides[get_current_user] = lambda: fake_wh_user
    try:
        tc = TestClient(app)
        resp = tc.get("/api/v1/batches")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1
        resp = tc.get(f"/api/v1/batches/{batch.id}")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------- UDI 字段 ----------


def test_udi_fields_create_update_roundtrip(client, db_session):
    """物料表单支持 batch_required 开关与 UDI 字段（成品），更新审计照旧。"""
    wh = _mk_wh(db_session)
    db_session.commit()
    resp = client.post(
        "/api/v1/materials",
        json={
            "code": "UDI1",
            "name": "成品监护仪",
            "unit": "台",
            "category": "finished",
            "batch_required": True,
            "udi_di": "69xxxxxxxxxx01",
            "udi_pi_rule": "批次号",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch_required"] is True
    assert body["udi_di"] == "69xxxxxxxxxx01"
    assert body["udi_pi_rule"] == "批次号"

    resp = client.put(
        f"/api/v1/materials/{body['id']}",
        json={
            "code": "UDI1",
            "name": "成品监护仪",
            "unit": "台",
            "category": "finished",
            "batch_required": False,
            "udi_di": "69xxxxxxxxxx02",
            "udi_pi_rule": "序列号",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["batch_required"] is False
    assert resp.json()["udi_di"] == "69xxxxxxxxxx02"
    # 更新审计留痕
    records = client.get(
        "/api/v1/audit/records", params={"doc_type": "material", "doc_id": body["id"]}
    ).json()
    fields = {r["field"] for r in records["items"]}
    assert "udi_di" in fields
    assert "batch_required" in fields


def test_material_defaults_without_batch_fields(client, db_session):
    """不传新字段时默认关批次、UDI 为空（v1 请求体兼容）。"""
    db_session.commit()
    resp = client.post(
        "/api/v1/materials",
        json={"code": "NB9", "name": "普通物料", "unit": "个", "category": "raw"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch_required"] is False
    assert body["udi_di"] is None
    assert body["udi_pi_rule"] is None
