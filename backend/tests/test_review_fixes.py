"""审查修复回归测试（v2 审查 4 项真 bug）。

1. MO 领料同物料多批次：逐行按 (material_id, batch_id) 独立扣减，不同批次严禁合并；
2. 批次号复用：仅 pending 批次可追加入库，qualified/rejected/closed 同批次号再入库 400；
3. dispose 权限键：端点挂 batch.dispose（qa/admin），production 403；
4. 角色生效文档：纯文档修正，无测试（鉴权本就以 DB 为准，即时生效）。
"""

from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import grant_supplier_cert, switch_user
from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.main import app
from erp_lite.models import (
    Batch,
    InventoryMove,
    ManufacturingOrder,
    Material,
    MaterialCategory,
    MoveType,
    Partner,
    PartnerType,
    Warehouse,
)
from erp_lite.models.base import InspStatus
from erp_lite.services.inventory import get_stock


# ---------- 造数助手（与 test_inspection_trace 同套路） ----------


def _mk_wh(db, code="WR"):
    wh = db.scalar(select(Warehouse).where(Warehouse.code == code))
    if wh is not None:
        return wh
    wh = Warehouse(code=code, name=f"仓库{code}")
    db.add(wh)
    return wh


def _mk_mat(db, code, batch_required=True, category=MaterialCategory.raw):
    mat = Material(
        code=code,
        name=f"物料{code}",
        unit="kg",
        category=category,
        batch_required=batch_required,
    )
    db.add(mat)
    return mat


def _mk_partner(db, code, ptype):
    p = Partner(code=code, name=f"单位{code}", type=ptype)
    db.add(p)
    if ptype == PartnerType.supplier:
        db.flush()
        grant_supplier_cert(db, p.id)
    return p


def _po_receive(client, db, mat, sup, wh, batch_no, qty, price):
    """PO 审核 + 按批次号入库一次，返回批次 ORM 对象。"""
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
    switch_user(client, "approver")
    client.post(f"/api/v1/purchase-orders/{po['id']}/approve")
    switch_user(client, "tester")  # v3：不得自审，PO 审核换 approver 后切回
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
    return resp, db.scalar(select(Batch).where(Batch.batch_no == batch_no))


def _inspect_pass(client, batch_id):
    """v3 双段制：qc 录入（suggest_conclusion）→ qa 换人判定 pass。"""
    resp = client.post(
        f"/api/v1/batches/{batch_id}/inspect",
        json={
            "items": [{"name": "外观", "result": "合格"}],
            "suggest_conclusion": "pass",
        },
    )
    assert resp.status_code == 200, resp.text
    switch_user(client, "qa_user")  # v3：不得自审，判定人 ≠ qc 录入人
    resp = client.post(
        f"/api/v1/batches/{batch_id}/judge",
        json={"conclusion": "pass"},
    )
    switch_user(client, "tester")
    assert resp.status_code == 200, resp.text


def _inspect_fail(client, batch_id):
    """v3 双段制：qc 录入（建议 fail）→ qa 换人判定 fail（批次 rejected）。"""
    resp = client.post(
        f"/api/v1/batches/{batch_id}/inspect",
        json={
            "items": [{"name": "外观", "result": "不合格"}],
            "suggest_conclusion": "fail",
        },
    )
    assert resp.status_code == 200, resp.text
    switch_user(client, "qa_user")  # v3：不得自审，判定人 ≠ qc 录入人
    resp = client.post(
        f"/api/v1/batches/{batch_id}/judge",
        json={"conclusion": "fail"},
    )
    switch_user(client, "tester")
    assert resp.status_code == 200, resp.text


def _mk_mo(client, product_id, wh_id, planned):
    resp = client.post(
        "/api/v1/manufacturing-orders",
        json={
            "material_id": product_id,
            "planned_qty": planned,
            "warehouse_id": wh_id,
        },
    )
    assert resp.status_code == 200, resp.text
    mo = resp.json()
    switch_user(client, "approver")
    resp = client.post(f"/api/v1/manufacturing-orders/{mo['id']}/approve")
    switch_user(client, "tester")  # v3：不得自审，MO 下达换 approver 后切回
    assert resp.status_code == 200, resp.text
    return mo


def _issue(client, mo_id, lines):
    return client.post(
        f"/api/v1/manufacturing-orders/{mo_id}/issue", json={"lines": lines}
    )


def _batch_moves(db, batch_id, move_type=None):
    stmt = select(InventoryMove).where(InventoryMove.batch_id == batch_id)
    if move_type is not None:
        stmt = stmt.where(InventoryMove.move_type == move_type)
    return db.scalars(stmt.order_by(InventoryMove.id)).all()


# ---------- Fix 1：MO 领料同物料多批次逐行扣减 ----------


def test_issue_same_material_two_batches_per_batch_moves(client, db_session):
    """同物料两批次领料：各批次独立扣减出库，不得把数量并到末行批次。

    修复前：batch_map 按物料去重，[RAW,4,LOT-A],[RAW,6,LOT-B] 全记 LOT-B，
    LOT-A 零流水且 LOT-B 超扣。
    """
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW2")
    fg = _mk_mat(db, "FG2", category=MaterialCategory.finished)  # 无 BOM 成品
    sup = _mk_partner(db, "SRW2", PartnerType.supplier)
    db.commit()

    _, lot_a = _po_receive(client, db, raw, sup, wh, "LOT-A2", 4, "2")
    _, lot_b = _po_receive(client, db, raw, sup, wh, "LOT-B2", 10, "3")
    _inspect_pass(client, lot_a.id)
    _inspect_pass(client, lot_b.id)
    mo = _mk_mo(client, fg.id, wh.id, 10)

    resp = _issue(
        client,
        mo["id"],
        [
            {"material_id": raw.id, "qty": 4, "batch_id": lot_a.id},
            {"material_id": raw.id, "qty": 6, "batch_id": lot_b.id},
        ],
    )
    assert resp.status_code == 200, resp.text

    # 归因：每批次各一条 mo_issue 流水，数量与行一致
    moves_a = _batch_moves(db, lot_a.id, MoveType.mo_issue)
    moves_b = _batch_moves(db, lot_b.id, MoveType.mo_issue)
    assert [Decimal(str(-m.qty)) for m in moves_a] == [Decimal("4")]
    assert [Decimal(str(-m.qty)) for m in moves_b] == [Decimal("6")]

    # 现存：LOT-A 全部领完为 0，LOT-B 余 4（修复前 LOT-B 超扣为 0）
    assert get_stock(db, raw.id, wh.id, batch_id=lot_a.id)[0] == Decimal("0")
    assert get_stock(db, raw.id, wh.id, batch_id=lot_b.id)[0] == Decimal("4")

    # 领料成本 = 4*2 + 6*3 = 26
    mo_orm = db.get(ManufacturingOrder, mo["id"])
    assert mo_orm.issued_qty_cost == Decimal("26.0000")


def test_issue_same_material_same_batch_lines_merge(client, db_session):
    """同物料同批次多行允许合并：汇总为一条流水一次性扣减。"""
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW3")
    fg = _mk_mat(db, "FG3", category=MaterialCategory.finished)
    sup = _mk_partner(db, "SRW3", PartnerType.supplier)
    db.commit()

    _, lot = _po_receive(client, db, raw, sup, wh, "LOT-C2", 10, "1")
    _inspect_pass(client, lot.id)
    mo = _mk_mo(client, fg.id, wh.id, 10)

    resp = _issue(
        client,
        mo["id"],
        [
            {"material_id": raw.id, "qty": 3, "batch_id": lot.id},
            {"material_id": raw.id, "qty": 2, "batch_id": lot.id},
        ],
    )
    assert resp.status_code == 200, resp.text

    moves = _batch_moves(db, lot.id, MoveType.mo_issue)
    assert [Decimal(str(-m.qty)) for m in moves] == [Decimal("5")]
    assert get_stock(db, raw.id, wh.id, batch_id=lot.id)[0] == Decimal("5")


def test_issue_two_batches_trace_forward_attribution(client, db_session):
    """追溯归因：成品批次正查能按正确数量追溯到各原料批次。"""
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW4")
    fg = _mk_mat(db, "FG4", category=MaterialCategory.finished)
    sup = _mk_partner(db, "SRW4", PartnerType.supplier)
    db.commit()

    _, lot_a = _po_receive(client, db, raw, sup, wh, "LOT-A4", 4, "2")
    _, lot_b = _po_receive(client, db, raw, sup, wh, "LOT-B4", 10, "3")
    _inspect_pass(client, lot_a.id)
    _inspect_pass(client, lot_b.id)
    mo = _mk_mo(client, fg.id, wh.id, 10)
    assert _issue(
        client,
        mo["id"],
        [
            {"material_id": raw.id, "qty": 4, "batch_id": lot_a.id},
            {"material_id": raw.id, "qty": 6, "batch_id": lot_b.id},
        ],
    ).status_code == 200

    resp = client.post(
        f"/api/v1/manufacturing-orders/{mo['id']}/receive",
        json={"qty": 4, "extra_cost": 0},
    )
    assert resp.status_code == 200, resp.text
    fg_batch = db.scalar(
        select(Batch).where(
            Batch.source_type == "manufacturing_order",
            Batch.material_id == fg.id,
        )
    )
    assert fg_batch is not None

    resp = client.get(f"/api/v1/batches/{fg_batch.id}/trace-forward")
    assert resp.status_code == 200, resp.text
    links = {(l["to_batch"]["batch_no"], Decimal(str(l["qty"]))) for l in resp.json()["links"]}
    assert links == {("LOT-A4", Decimal("4")), ("LOT-B4", Decimal("6"))}


# ---------- Fix 2：批次号复用仅限 pending ----------


def test_receive_qualified_batch_no_reuse_400(client, db_session):
    """qualified 批次号再入库 → 400（防新增量免检放行），批次不新增。"""
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW5")
    sup = _mk_partner(db, "SRW5", PartnerType.supplier)
    db.commit()

    _, batch = _po_receive(client, db, raw, sup, wh, "LOT-Q2", 10, "2")
    _inspect_pass(client, batch.id)
    assert batch.insp_status == InspStatus.qualified

    resp, _ = _po_receive(client, db, raw, sup, wh, "LOT-Q2", 5, "2")
    assert resp.status_code == 400, resp.text
    # v3：批次号一律不复用，detail 提示「不复用」
    assert "不复用" in resp.json()["detail"]
    assert db.scalar(select(Batch.batch_no).where(Batch.batch_no == "LOT-Q2")) == "LOT-Q2"
    assert len(db.scalars(select(Batch).where(Batch.batch_no == "LOT-Q2")).all()) == 1


def test_receive_pending_batch_no_reuse_400_not_reused(client, db_session):
    """v3 语义反转：pending 批次号再入库也 400（批次号不复用），批次不新增、存量不变。"""
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW6")
    sup = _mk_partner(db, "SRW6", PartnerType.supplier)
    db.commit()

    resp, batch = _po_receive(client, db, raw, sup, wh, "LOT-P2", 10, "2")
    assert resp.status_code == 200
    assert batch.insp_status == InspStatus.pending

    resp, _ = _po_receive(client, db, raw, sup, wh, "LOT-P2", 5, "2")
    assert resp.status_code == 400, resp.text
    # v3：批次号不复用（原 pending 可追加复用语义已废除），提示换新批次号
    assert "不复用" in resp.json()["detail"]
    batches = db.scalars(select(Batch).where(Batch.batch_no == "LOT-P2")).all()
    assert len(batches) == 1
    assert batches[0].insp_status == InspStatus.pending
    # v3：拒绝入库后原批次存量不变（不再追加 5）
    assert get_stock(db, raw.id, wh.id, batch_id=batch.id)[0] == Decimal("10")


def test_receive_closed_batch_no_reuse_400(client, db_session):
    """closed（处置完毕）批次号再入库 → 400，同 qualified 口径。"""
    db = db_session
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW7")
    sup = _mk_partner(db, "SRW7", PartnerType.supplier)
    db.commit()

    _, batch = _po_receive(client, db, raw, sup, wh, "LOT-X2", 10, "2")
    _inspect_fail(client, batch.id)  # v3：双段制 inspect+judge 换人判定
    resp = client.post(
        f"/api/v1/batches/{batch.id}/dispose",
        json={"disposition": "return", "remark": "退货"},
    )
    assert resp.status_code == 200, resp.text
    assert batch.insp_status == InspStatus.closed

    resp, _ = _po_receive(client, db, raw, sup, wh, "LOT-X2", 5, "2")
    assert resp.status_code == 400, resp.text
    # v3：批次号不复用
    assert "不复用" in resp.json()["detail"]


# ---------- Fix 3：dispose 挂 batch.dispose 动作键 ----------


def _switch_role(role):
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=99, username=f"{role}_user", role=role
    )


def _mk_rejected_batch(client, db):
    wh = _mk_wh(db)
    raw = _mk_mat(db, "RW8")
    sup = _mk_partner(db, "SRW8", PartnerType.supplier)
    db.commit()
    _, batch = _po_receive(client, db, raw, sup, wh, "LOT-R8", 10, "2")
    _inspect_fail(client, batch.id)  # v3：双段制 inspect+judge 换人判定
    return batch


def test_production_dispose_403(client, db_session):
    """dispose 动作键 batch.dispose：production 越权 403。"""
    db = db_session
    batch = _mk_rejected_batch(client, db)
    _switch_role("production")
    try:
        resp = client.post(
            f"/api/v1/batches/{batch.id}/dispose",
            json={"disposition": "return", "remark": "越权尝试"},
        )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_qa_dispose_200_with_dedicated_key(client, db_session):
    """dispose 动作键 batch.dispose：qa 持有，处置成功闭环。"""
    db = db_session
    batch = _mk_rejected_batch(client, db)
    raw = db.get(Material, batch.material_id)
    wh = db.scalar(select(Warehouse).where(Warehouse.code == "WR"))
    stock_before = get_stock(db, raw.id, wh.id, batch_id=batch.id)[0]
    assert stock_before > 0
    _switch_role("qa")
    try:
        resp = client.post(
            f"/api/v1/batches/{batch.id}/dispose",
            json={"disposition": "destroy", "remark": "销毁"},
        )
        assert resp.status_code == 200, resp.text
        assert batch.insp_status == InspStatus.closed
        # v3：destroy 报废清现存并留损失（loss_amount>0、现存清零）
        assert batch.loss_amount > 0
        assert get_stock(db, raw.id, wh.id, batch_id=batch.id)[0] == Decimal("0")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
