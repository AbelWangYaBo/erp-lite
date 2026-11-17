"""检验放行与批次追溯测试（V2-S3）。

覆盖：QA 检验判定（pass→qualified / fail→rejected）、不合格处置（return/destroy→closed）、
领料/出库合格放行拦截（pending/rejected/closed 一律 400，qualified 放行）、
MRP 只计 qualified 批次、批次正反向追溯（RAW→SEMI→FG 两层 BOM）。
"""

from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from conftest import grant_supplier_cert
from erp_lite.main import app
from erp_lite.models import (
    AuditRecord,
    Batch,
    InspectionRecord,
    Material,
    MaterialCategory,
    OperationLog,
)
from erp_lite.models.base import InspStatus
from erp_lite.models.master_data import Partner, PartnerType, Warehouse


# ---------- 造数助手 ----------


def _mk_wh(db, code="WQ"):
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
        grant_supplier_cert(db, p.id)  # V2-S4 资质拦截：供应商默认资质合规
    return p


def _receive_batch(client, db, mat_code, batch_no, qty=10, price="2"):
    """建批次物料+供应商，PO 审核、入库一个批次，返回 (mat, sup, wh, batch)。"""
    wh = _mk_wh(db)
    mat = _mk_mat(db, mat_code)
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
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    assert batch is not None
    assert batch.insp_status == InspStatus.pending
    return mat, sup, wh, batch


def _inspect(client, batch_id, conclusion="pass", items=None, remark=None):
    if items is None:
        items = [
            {"name": "外观", "result": "合格" if conclusion == "pass" else "不合格"},
            {"name": "尺寸", "result": "合格" if conclusion == "pass" else "超差"},
        ]
    payload = {"items": items, "conclusion": conclusion}
    if remark is not None:
        payload["remark"] = remark
    return client.post(f"/api/v1/batches/{batch_id}/inspect", json=payload)


def _dispose(client, batch_id, disposition="return", remark="退货处理"):
    return client.post(
        f"/api/v1/batches/{batch_id}/dispose",
        json={"disposition": disposition, "remark": remark},
    )


def _switch_role(role):
    fake = SimpleNamespace(id=99, username=f"{role}_user", role=role)
    app.dependency_overrides[get_current_user] = lambda: fake
    return fake


def _restore_role():
    app.dependency_overrides.pop(get_current_user, None)


def _mk_so(client, db, mat, wh_id, qty=3, price=9):
    cus = _mk_partner(db, f"C{mat.code}", PartnerType.customer)
    db.commit()
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": cus.id,
            "order_date": "2026-11-17",
            "lines": [
                {
                    "material_id": mat.id,
                    "qty": qty,
                    "unit_price": price,
                    "warehouse_id": wh_id,
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    so = resp.json()
    client.post(f"/api/v1/sales-orders/{so['id']}/approve")
    return so


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
    resp = client.post(f"/api/v1/manufacturing-orders/{mo['id']}/approve")
    assert resp.status_code == 200, resp.text
    return mo


def _issue(client, mo_id, lines):
    return client.post(
        f"/api/v1/manufacturing-orders/{mo_id}/issue", json={"lines": lines}
    )


def _receive_mo(client, mo_id, qty):
    return client.post(
        f"/api/v1/manufacturing-orders/{mo_id}/receive",
        json={"qty": qty, "extra_cost": 0},
    )


# ---------- 检验判定 ----------


def test_receive_batch_starts_pending(client, db_session):
    """采购入库批次初始 pending（放行拦截的前提）。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ1", "IT-P0")
    assert batch.insp_status == InspStatus.pending


def test_inspect_pass_sets_qualified_and_writes_record(client, db_session):
    """QA 判 pass → 批次 qualified，检验记录（检项/结论/检验人）落库。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ2", "IT-A1")
    resp = _inspect(client, batch.id, conclusion="pass", remark="全项合格")
    assert resp.status_code == 200, resp.text
    db_session.refresh(batch)
    assert batch.insp_status == InspStatus.qualified
    rec = db_session.scalar(
        select(InspectionRecord).where(InspectionRecord.batch_id == batch.id)
    )
    assert rec is not None
    assert rec.conclusion == "pass"
    assert rec.inspected_at is not None
    assert [i["name"] for i in rec.items_json] == ["外观", "尺寸"]
    assert rec.remark == "全项合格"
    assert rec.inspector == "tester"


def test_inspect_fail_sets_rejected(client, db_session):
    """QA 判 fail → 批次 rejected（隔离）。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ3", "IT-A2")
    resp = _inspect(client, batch.id, conclusion="fail")
    assert resp.status_code == 200, resp.text
    db_session.refresh(batch)
    assert batch.insp_status == InspStatus.rejected
    rec = db_session.scalar(
        select(InspectionRecord).where(InspectionRecord.batch_id == batch.id)
    )
    assert rec.conclusion == "fail"


def test_reinspect_qualified_to_rejected(client, db_session):
    """复判：已合格批次可再判 fail → rejected，记录保留多条历史。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ4", "IT-A3")
    assert _inspect(client, batch.id, "pass").status_code == 200
    assert _inspect(client, batch.id, "fail").status_code == 200
    db_session.refresh(batch)
    assert batch.insp_status == InspStatus.rejected
    records = db_session.scalars(
        select(InspectionRecord).where(InspectionRecord.batch_id == batch.id)
    ).all()
    assert len(records) == 2
    assert sorted(r.conclusion for r in records) == ["fail", "pass"]


def test_inspect_closed_batch_400(client, db_session):
    """已关闭（closed）批次不可再检验判定 → 400。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ5", "IT-A4")
    assert _inspect(client, batch.id, "fail").status_code == 200
    assert _dispose(client, batch.id).status_code == 200
    resp = _inspect(client, batch.id, "pass")
    assert resp.status_code == 400
    assert "关闭" in resp.json()["detail"]


def test_inspect_batch_404(client, db_session):
    db_session.commit()
    resp = _inspect(client, 999999, "pass")
    assert resp.status_code == 404


def test_inspect_invalid_conclusion_422(client, db_session):
    """conclusion 非法值 → 422（枚举约束）。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ6", "IT-A5")
    resp = client.post(
        f"/api/v1/batches/{batch.id}/inspect",
        json={"items": [{"name": "外观", "result": "合格"}], "conclusion": "ok"},
    )
    assert resp.status_code == 422


def test_inspect_requires_qa_role_403(client, db_session):
    """production / warehouse / purchase 判检验 → 403。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ7", "IT-A6")
    for role in ("production", "warehouse", "purchase"):
        _switch_role(role)
        try:
            tc = TestClient(app)
            resp = tc.post(
                f"/api/v1/batches/{batch.id}/inspect",
                json={"items": [{"name": "外观", "result": "合格"}], "conclusion": "pass"},
            )
            assert resp.status_code == 403, (role, resp.text)
        finally:
            _restore_role()


def test_inspect_qa_role_allowed(client, db_session):
    """qa 角色可判定。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ8", "IT-A7")
    _switch_role("qa")
    try:
        tc = TestClient(app)
        resp = tc.post(
            f"/api/v1/batches/{batch.id}/inspect",
            json={"items": [{"name": "外观", "result": "合格"}], "conclusion": "pass"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        _restore_role()
    db_session.refresh(batch)
    assert batch.insp_status == InspStatus.qualified


def test_inspect_writes_audit_and_op_log(client, db_session):
    """判定动作留痕：审计 action=inspection（含状态流转）+ 操作日志。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "IQ9", "IT-A8")
    assert _inspect(client, batch.id, "pass").status_code == 200
    audits = db_session.scalars(
        select(AuditRecord).where(
            AuditRecord.doc_type == "batch", AuditRecord.doc_id == batch.id
        )
    ).all()
    assert any(a.action == "inspection" for a in audits)
    insp_audit = next(a for a in audits if a.action == "inspection")
    assert insp_audit.old_value == "pending"
    assert insp_audit.new_value == "qualified"
    log = db_session.scalar(
        select(OperationLog).where(OperationLog.action == "batch_inspect")
    )
    assert log is not None
    assert batch.batch_no in log.detail


# ---------- 不合格处置 ----------


def test_dispose_rejected_return_closes_batch(client, db_session):
    """rejected 批次处置退货 → disposition=return、insp_status=closed。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "DQ1", "IT-D1")
    assert _inspect(client, batch.id, "fail").status_code == 200
    resp = _dispose(client, batch.id, "return", "整批退供应商")
    assert resp.status_code == 200, resp.text
    db_session.refresh(batch)
    assert batch.insp_status == InspStatus.closed
    assert batch.disposition == "return"
    assert batch.disposition_remark == "整批退供应商"
    # 处置留痕
    audits = db_session.scalars(
        select(AuditRecord).where(
            AuditRecord.doc_type == "batch", AuditRecord.doc_id == batch.id
        )
    ).all()
    assert any(a.action == "dispose" for a in audits)


def test_dispose_rejected_destroy_closes_batch(client, db_session):
    """rejected 批次处置销毁 → disposition=destroy、closed。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "DQ2", "IT-D2")
    assert _inspect(client, batch.id, "fail").status_code == 200
    resp = _dispose(client, batch.id, "destroy", "现场销毁")
    assert resp.status_code == 200, resp.text
    db_session.refresh(batch)
    assert batch.disposition == "destroy"
    assert batch.insp_status == InspStatus.closed


def test_dispose_pending_batch_400(client, db_session):
    """pending 批次不可处置（仅 rejected）。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "DQ3", "IT-D3")
    resp = _dispose(client, batch.id)
    assert resp.status_code == 400
    assert "不合格" in resp.json()["detail"]


def test_dispose_qualified_batch_400(client, db_session):
    """qualified 批次不可处置。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "DQ4", "IT-D4")
    assert _inspect(client, batch.id, "pass").status_code == 200
    resp = _dispose(client, batch.id)
    assert resp.status_code == 400


def test_dispose_twice_400(client, db_session):
    """已处置（closed）批次不可重复处置。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "DQ5", "IT-D5")
    assert _inspect(client, batch.id, "fail").status_code == 200
    assert _dispose(client, batch.id).status_code == 200
    resp = _dispose(client, batch.id)
    assert resp.status_code == 400


# ---------- 放行拦截（领料/出库四状态对照） ----------


def _mk_so_for_batch(client, db, batch, qty=3):
    wh = db.get(Warehouse, db.scalar(select(Warehouse.id).limit(1)))
    mat = db.get(Material, batch.material_id)
    return _mk_so(client, db, mat, wh.id, qty=qty)


def test_ship_pending_batch_400_pending_hint(client, db_session):
    """pending 批次出库 → 400，提示「待检」。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ1", "IT-G1")
    so = _mk_so_for_batch(client, db_session, batch)
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
    assert resp.status_code == 400
    assert "待检" in resp.json()["detail"]


def test_ship_rejected_batch_400_quarantine_hint(client, db_session):
    """rejected 批次出库 → 400，提示「隔离」。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ2", "IT-G2")
    assert _inspect(client, batch.id, "fail").status_code == 200
    so = _mk_so_for_batch(client, db_session, batch)
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
    assert resp.status_code == 400
    assert "隔离" in resp.json()["detail"]


def test_ship_closed_batch_400(client, db_session):
    """closed 批次出库 → 400。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ3", "IT-G3")
    assert _inspect(client, batch.id, "fail").status_code == 200
    assert _dispose(client, batch.id).status_code == 200
    so = _mk_so_for_batch(client, db_session, batch)
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
    assert resp.status_code == 400


def test_ship_qualified_batch_200(client, db_session):
    """qualified 批次出库放行 → 200，批次现存扣减。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ4", "IT-G4")
    assert _inspect(client, batch.id, "pass").status_code == 200
    so = _mk_so_for_batch(client, db_session, batch)
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
    from erp_lite.services.batches import get_batch_stock

    qty, _ = get_batch_stock(db_session, batch.id)
    assert qty == Decimal("7")


def test_issue_pending_then_pass_then_200(client, db_session):
    """领料链路：pending → 400 → QA 判 pass → 200。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ5", "IT-G5")
    product = _mk_mat(
        db_session, "PGQ5", batch_required=False, category=MaterialCategory.finished
    )
    db_session.commit()
    mo = _mk_mo(client, product.id, wh.id, 10)
    resp = _issue(client, mo["id"], [{"material_id": mat.id, "qty": 4, "batch_id": batch.id}])
    assert resp.status_code == 400
    assert "待检" in resp.json()["detail"]
    # QA 判合格后放行
    assert _inspect(client, batch.id, "pass").status_code == 200
    resp = _issue(client, mo["id"], [{"material_id": mat.id, "qty": 4, "batch_id": batch.id}])
    assert resp.status_code == 200, resp.text


def test_issue_rejected_batch_400(client, db_session):
    """rejected 批次领料 → 400，提示「隔离」。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "GQ6", "IT-G6")
    assert _inspect(client, batch.id, "fail").status_code == 200
    product = _mk_mat(
        db_session, "PGQ6", batch_required=False, category=MaterialCategory.finished
    )
    db_session.commit()
    mo = _mk_mo(client, product.id, wh.id, 10)
    resp = _issue(client, mo["id"], [{"material_id": mat.id, "qty": 4, "batch_id": batch.id}])
    assert resp.status_code == 400
    assert "隔离" in resp.json()["detail"]


def test_mo_receipt_batch_qualified_shippable(client, db_session):
    """MO 完工成品批次保持 qualified（无需检验即可出库），supplier 为空。"""
    wh = _mk_wh(db_session, "WQ2")
    product = _mk_mat(
        db_session, "FGQ1", batch_required=True, category=MaterialCategory.finished
    )
    db_session.commit()
    mo = _mk_mo(client, product.id, wh.id, 5)
    resp = _receive_mo(client, mo["id"], 5)
    assert resp.status_code == 200, resp.text
    batch = db_session.scalar(select(Batch).where(Batch.material_id == product.id))
    assert batch.insp_status == InspStatus.qualified
    assert batch.supplier_id is None
    # 成品批次直接出库放行
    so = _mk_so(client, db_session, product, wh.id, qty=2)
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[
            {
                "line_id": so["lines"][0]["id"],
                "qty": 2,
                "warehouse_id": wh.id,
                "batch_id": batch.id,
            }
        ],
    )
    assert resp.status_code == 200, resp.text


# ---------- MRP 口径：只计 qualified ----------


def test_mrp_excludes_pending_batch(client, db_session):
    """pending 批次不计入 MRP 现存：需求 10 不被 pending 库存抵扣。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "MQ1", "IT-M1")
    suggestions = __import__("erp_lite.services.mrp", fromlist=["run_mrp"]).run_mrp(
        db_session, [(mat.id, Decimal("10"))]
    )
    raw = [s for s in suggestions if s["material_id"] == mat.id]
    assert raw, "pending 批次不应抵扣净需求"
    assert raw[0]["net_qty"] == Decimal("10")


def test_mrp_counts_qualified_after_pass(client, db_session):
    """QA 判合格后，同需求被 qualified 批次抵扣 → 无净需求建议。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "MQ2", "IT-M2")
    assert _inspect(client, batch.id, "pass").status_code == 200
    from erp_lite.services.mrp import run_mrp

    suggestions = run_mrp(db_session, [(mat.id, Decimal("10"))])
    raw = [s for s in suggestions if s["material_id"] == mat.id]
    assert raw == []


# ---------- 批次正反向追溯 ----------


def _build_two_layer_chain(client, db):
    """构造 RAW →（MO1 领料/完工）→ SEMI →（MO2 领料/完工）→ FG 两层生产链路。

    FG 挂 BOM（子件 SEMI）；MO 领料均显式指定叶子批次，使批次经 mo_issue
    流水在两层间传递。返回 (raw_batch, semi_batch, fg_batch, sup, mo1, mo2)。
    """
    # 原料批次：入库 + QA 合格
    raw_mat, sup, wh, raw_batch = _receive_batch(client, db, "TR", "LOT-TR")
    assert _inspect(client, raw_batch.id, "pass").status_code == 200
    # 半成品（无 BOM，叶子物料）：MO1 领 RAW 批次 → 完工生成 SEMI 批次
    semi = _mk_mat(db, "TSEMI", category=MaterialCategory.semi)
    fg = _mk_mat(db, "TFG", category=MaterialCategory.finished)
    db.commit()
    assert client.put(
        f"/api/v1/boms/{fg.id}", json={"lines": [{"material_id": semi.id, "qty_per": 1}]}
    ).status_code == 200
    mo1 = _mk_mo(client, semi.id, wh.id, 4)
    resp = _issue(
        client, mo1["id"], [{"material_id": raw_mat.id, "qty": 4, "batch_id": raw_batch.id}]
    )
    assert resp.status_code == 200, resp.text
    resp = _receive_mo(client, mo1["id"], 4)
    assert resp.status_code == 200, resp.text
    semi_batch = db.scalar(select(Batch).where(Batch.material_id == semi.id))
    assert semi_batch is not None
    # 成品：MO2 领 SEMI 批次 → 完工生成 FG 批次
    mo2 = _mk_mo(client, fg.id, wh.id, 2)
    resp = _issue(
        client, mo2["id"], [{"material_id": semi.id, "qty": 2, "batch_id": semi_batch.id}]
    )
    assert resp.status_code == 200, resp.text
    resp = _receive_mo(client, mo2["id"], 2)
    assert resp.status_code == 200, resp.text
    fg_batch = db.scalar(select(Batch).where(Batch.material_id == fg.id))
    assert fg_batch is not None
    return raw_batch, semi_batch, fg_batch, sup, mo1, mo2


def test_trace_forward_two_layers(client, db_session):
    """FG 批次正查：两层展开到 SEMI 批次与 RAW 批次，RAW 带供应商。"""
    raw_batch, semi_batch, fg_batch, sup, mo1, mo2 = _build_two_layer_chain(
        client, db_session
    )
    resp = client.get(f"/api/v1/batches/{fg_batch.id}/trace-forward")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch"]["id"] == fg_batch.id
    links = {l["to_batch"]["id"]: l for l in body["links"]}
    # 第一层：FG ← SEMI 批次（经 MO2 领料）
    assert semi_batch.id in links
    assert links[semi_batch.id]["via_doc"] == mo2["doc_no"]
    assert Decimal(str(links[semi_batch.id]["qty"])) == Decimal("2")
    # 第二层：← RAW 批次（经 MO1 领料），带供应商
    assert raw_batch.id in links
    assert links[raw_batch.id]["via_doc"] == mo1["doc_no"]
    assert Decimal(str(links[raw_batch.id]["qty"])) == Decimal("4")
    assert links[raw_batch.id]["partner"] == sup.name


def test_trace_backward_raw_to_fg(client, db_session):
    """RAW 批次反查：经 mo_issue 流水找到 MO → FG 成品批次。"""
    raw_batch, semi_batch, fg_batch, sup, mo1, mo2 = _build_two_layer_chain(
        client, db_session
    )
    resp = client.get(f"/api/v1/batches/{raw_batch.id}/trace-backward")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch"]["id"] == raw_batch.id
    # 递归两层：RAW →（MO1）SEMI 批次 →（MO2）FG 成品批次
    links = {l["to_batch"]["id"]: l for l in body["links"]}
    assert set(links) == {semi_batch.id, fg_batch.id}
    assert links[semi_batch.id]["via_doc"] == mo1["doc_no"]
    assert Decimal(str(links[semi_batch.id]["qty"])) == Decimal("4")
    link = links[fg_batch.id]
    assert link["to_batch"]["batch_no"] == f"{mo2['doc_no']}-1"
    assert link["via_doc"] == mo2["doc_no"]
    assert Decimal(str(link["qty"])) == Decimal("2")


def test_trace_forward_raw_batch_400_direction(client, db_session):
    """原料批次正向追溯方向不当 → 400（应用反向追溯）。"""
    mat, sup, wh, batch = _receive_batch(client, db_session, "TW1", "IT-W1")
    resp = client.get(f"/api/v1/batches/{batch.id}/trace-forward")
    assert resp.status_code == 400
    assert "方向" in resp.json()["detail"]


def test_trace_backward_fg_batch_400_direction(client, db_session):
    """无领料流向的成品批次反向追溯方向不当 → 400（应用正向追溯）。"""
    wh = _mk_wh(db_session, "WQ3")
    product = _mk_mat(
        db_session, "FGW1", batch_required=True, category=MaterialCategory.finished
    )
    db_session.commit()
    mo = _mk_mo(client, product.id, wh.id, 3)
    assert _receive_mo(client, mo["id"], 3).status_code == 200
    batch = db_session.scalar(select(Batch).where(Batch.material_id == product.id))
    resp = client.get(f"/api/v1/batches/{batch.id}/trace-backward")
    assert resp.status_code == 400
    assert "方向" in resp.json()["detail"]


def test_trace_batch_404(client, db_session):
    db_session.commit()
    assert client.get("/api/v1/batches/999999/trace-forward").status_code == 404
    assert client.get("/api/v1/batches/999999/trace-backward").status_code == 404


def test_issue_intermediate_batch_with_bom(client, db_session):
    """v2-S6：中间件（SEMI）自带 BOM 时按批次直领不再向下展开（整批领用）。

    修复前：issue 行指定 batch_id 仍按 BOM 递归展开到 RAW 叶子，
    导致 SEMI 批次无法被 FG 工单领用（两层链在 MRP 场景下断裂）。
    """
    raw_mat, sup, wh, raw_batch = _receive_batch(client, db_session, "IB1", "LOT-IB")
    assert _inspect(client, raw_batch.id, "pass").status_code == 200
    semi = _mk_mat(db_session, "IBSEMI", category=MaterialCategory.semi)
    fg = _mk_mat(db_session, "IBFG", category=MaterialCategory.finished)
    db_session.commit()
    # SEMI 自带 BOM（2×RAW）：MRP 可对 SEMI 出 make 建议的真实两层结构
    assert client.put(
        f"/api/v1/boms/{semi.id}",
        json={"lines": [{"material_id": raw_mat.id, "qty_per": 2}]},
    ).status_code == 200
    assert client.put(
        f"/api/v1/boms/{fg.id}",
        json={"lines": [{"material_id": semi.id, "qty_per": 1}]},
    ).status_code == 200
    mo1 = _mk_mo(client, semi.id, wh.id, 5)
    assert _issue(
        client, mo1["id"], [{"material_id": raw_mat.id, "qty": 10, "batch_id": raw_batch.id}]
    ).status_code == 200
    assert _receive_mo(client, mo1["id"], 5).status_code == 200
    semi_batch = db_session.scalar(select(Batch).where(Batch.material_id == semi.id))
    mo2 = _mk_mo(client, fg.id, wh.id, 5)
    resp = _issue(
        client, mo2["id"], [{"material_id": semi.id, "qty": 5, "batch_id": semi_batch.id}]
    )
    assert resp.status_code == 200, resp.text
    assert _receive_mo(client, mo2["id"], 5).status_code == 200
    fg_batch = db_session.scalar(select(Batch).where(Batch.material_id == fg.id))
    links = client.get(f"/api/v1/batches/{fg_batch.id}/trace-forward").json()["links"]
    nos = [l["to_batch"]["batch_no"] for l in links]
    assert semi_batch.batch_no in nos and raw_batch.batch_no in nos
