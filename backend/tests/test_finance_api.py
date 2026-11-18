"""S5 财务 API 测试：应收应付列表、收付款核销、对账单。

覆盖：核销后 settled_amount/status 正确、超额/错配/跨 partner 400、
部分核销→再核余款→settled、对账单手算核对、分页与过滤。
"""

from decimal import Decimal

from sqlalchemy import select

from conftest import grant_supplier_cert, switch_user
from erp_lite.models import (
    Material,
    MaterialCategory,
    Partner,
    PartnerType,
    Payable,
    Receivable,
    Settlement,
    Warehouse,
)


def _seed(db_session):
    """幂等造数：按 code/name get-or-create，避免重复调用插入重复行。"""

    def get_or_create(model, **kw):
        obj = db_session.scalar(select(model).filter_by(**kw))
        if obj is None:
            obj = model(**kw)
            db_session.add(obj)
            db_session.flush()
        return obj

    wh = get_or_create(Warehouse, code="W1", name="一号仓")
    mat = get_or_create(Material, code="M1", name="铜锭", unit="kg", category=MaterialCategory.raw)
    sup = get_or_create(Partner, code="SUP1", name="华东供应商", type=PartnerType.supplier)
    sup2 = get_or_create(Partner, code="SUP2", name="南方供应商", type=PartnerType.supplier)
    cus = get_or_create(Partner, code="CUS1", name="北方客户", type=PartnerType.customer)
    db_session.commit()
    grant_supplier_cert(db_session, sup.id)  # V2-S4 资质拦截：供应商默认资质合规
    grant_supplier_cert(db_session, sup2.id)
    return wh, mat, sup, sup2, cus


def _purchase_in(client, db_session, partner_id, qty=100, price=10):
    """创建采购单→审核→全额入库，返回 (po, 入库金额)。"""
    wh, mat, *_ = _seed(db_session)
    resp = client.post(
        "/api/v1/purchase-orders",
        json={
            "partner_id": partner_id,
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
    assert client.post(f"/api/v1/purchase-orders/{po['id']}/approve").status_code == 200
    switch_user(client, "tester")  # v3：不得自审，PO 审核换 approver 后切回
    resp = client.post(
        f"/api/v1/purchase-orders/{po['id']}/receive",
        json=[{"line_id": po["lines"][0]["id"], "qty": qty, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    return po, Decimal(str(qty)) * Decimal(str(price))


def _sale_out(client, db_session, partner_id, qty=20, price=25):
    """备货→创建销售单→审核→全额出库，返回 (so, 出库金额)。"""
    wh, mat, sup, *_ = _seed(db_session)
    # 备货：向 sup 采购入库
    _purchase_in(client, db_session, sup.id, qty=qty, price=10)
    resp = client.post(
        "/api/v1/sales-orders",
        json={
            "partner_id": partner_id,
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
    so = resp.json()
    switch_user(client, "approver")
    assert client.post(f"/api/v1/sales-orders/{so['id']}/approve").status_code == 200
    switch_user(client, "tester")  # v3：不得自审，SO 审核换 approver 后切回
    resp = client.post(
        f"/api/v1/sales-orders/{so['id']}/ship",
        json=[{"line_id": so["lines"][0]["id"], "qty": qty, "warehouse_id": wh.id}],
    )
    assert resp.status_code == 200, resp.text
    return so, Decimal(str(qty)) * Decimal(str(price))


def _settle(client, *, type_, partner_id, target_type, target_id, amount):
    return client.post(
        "/api/v1/settlements",
        json={
            "type": type_,
            "partner_id": partner_id,
            "date": "2026-11-17",
            "lines": [{"target_type": target_type, "target_id": target_id, "amount": str(amount)}],
        },
    )


# ---------- 应付/应收生成 + 核销主流程 ----------


def test_receive_generates_payable_then_partial_payment(client, db_session):
    """采购入库→Payable 生成→部分付款核销→settled_amount/status 正确。"""
    _, _, sup, _, _ = _seed(db_session)
    po, amount = _purchase_in(client, db_session, sup.id)
    assert amount == Decimal("1000")

    ap = db_session.scalar(select(Payable))
    assert ap is not None
    assert Decimal(str(ap.amount)) == Decimal("1000")
    assert ap.status == "open"
    assert Decimal(str(ap.settled_amount)) == Decimal("0")

    resp = _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=400)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "payment"
    assert Decimal(str(body["amount"])) == Decimal("400")
    assert len(body["lines"]) == 1
    assert body["partner_name"] == "华东供应商"

    db_session.refresh(ap)
    assert Decimal(str(ap.settled_amount)) == Decimal("400")
    assert ap.status == "partial"


def test_settle_remaining_after_partial_becomes_settled(client, db_session):
    """部分核销后 status=partial，再核余款→settled。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))

    r1 = _settle(client, type_="payment", partner_id=sup.id,
                 target_type="ap", target_id=ap.id, amount=400)
    assert r1.status_code == 200
    db_session.refresh(ap)
    assert ap.status == "partial"

    r2 = _settle(client, type_="payment", partner_id=sup.id,
                 target_type="ap", target_id=ap.id, amount=600)
    assert r2.status_code == 200, r2.text
    db_session.refresh(ap)
    assert Decimal(str(ap.settled_amount)) == Decimal("1000")
    assert ap.status == "settled"

    # 核销记录两条，均落库
    settlements = db_session.scalars(select(Settlement)).all()
    assert len(settlements) == 2


def test_receipt_settles_receivable(client, db_session):
    """销售出库→Receivable 生成→收款核销→settled_amount/status 正确。"""
    _, _, _, _, cus = _seed(db_session)
    _, amount = _sale_out(client, db_session, cus.id)
    assert amount == Decimal("500")

    ar = db_session.scalar(select(Receivable))
    assert Decimal(str(ar.amount)) == Decimal("500")
    assert ar.status == "open"

    resp = _settle(client, type_="receipt", partner_id=cus.id,
                   target_type="ar", target_id=ar.id, amount=500)
    assert resp.status_code == 200, resp.text
    db_session.refresh(ar)
    assert Decimal(str(ar.settled_amount)) == Decimal("500")
    assert ar.status == "settled"


# ---------- 校验类 400/404/422 ----------


def test_over_settle_400(client, db_session):
    """超额核销 400：累计核销不得超过剩余额。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    resp = _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=1001)
    assert resp.status_code == 400
    assert "超过" in resp.json()["detail"]
    db_session.refresh(ap)
    assert Decimal(str(ap.settled_amount)) == Decimal("0")
    assert ap.status == "open"


def test_cumulative_over_settle_400(client, db_session):
    """两笔累计超额同样 400（第二笔失败后第一笔已生效）。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    assert _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=700).status_code == 200
    resp = _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=400)
    assert resp.status_code == 400
    db_session.refresh(ap)
    assert Decimal(str(ap.settled_amount)) == Decimal("700")
    assert ap.status == "partial"


def test_type_mismatch_400(client, db_session):
    """type 与 target 错配：payment 不能核 ar，receipt 不能核 ap。"""
    _, _, sup, _, cus = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    resp = _settle(client, type_="receipt", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=100)
    assert resp.status_code == 400
    assert "匹配" in resp.json()["detail"]


def test_cross_partner_400(client, db_session):
    """核销单据不属于该 partner → 400。"""
    _, _, sup, sup2, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    resp = _settle(client, type_="payment", partner_id=sup2.id,
                   target_type="ap", target_id=ap.id, amount=100)
    assert resp.status_code == 400
    assert "不属于" in resp.json()["detail"]


def test_zero_or_negative_amount_rejected(client, db_session):
    """金额 <=0 拒绝。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    resp = _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=0)
    assert resp.status_code == 422


def test_settle_nonexistent_target_404(client, db_session):
    _, _, sup, _, _ = _seed(db_session)
    resp = _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=99999, amount=100)
    assert resp.status_code == 404


# ---------- 列表与分页过滤 ----------


def test_payables_list_pagination_and_filters(client, db_session):
    """应付列表：分页 + partner_id/status 过滤 + partner 名称。"""
    _, _, sup, sup2, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id, qty=100, price=10)  # 1000
    _purchase_in(client, db_session, sup.id, qty=60, price=10)  # 600
    _purchase_in(client, db_session, sup2.id, qty=30, price=10)  # 300
    aps = {i: p for i, p in enumerate(db_session.scalars(select(Payable)).all())}
    first_ap = aps[0]

    r1 = _settle(client, type_="payment", partner_id=sup.id,
                 target_type="ap", target_id=first_ap.id, amount=1000)
    assert r1.status_code == 200

    resp = client.get("/api/v1/payables", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert {it["partner_name"] for it in body["items"]} <= {"华东供应商", "南方供应商"}

    resp = client.get("/api/v1/payables", params={"partner_id": sup.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(it["partner_id"] == sup.id for it in body["items"])

    resp = client.get("/api/v1/payables", params={"status": "settled"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "settled"
    assert Decimal(str(body["items"][0]["settled_amount"])) == Decimal("1000")

    resp = client.get("/api/v1/payables", params={"status": "open"})
    assert resp.json()["total"] == 2


def test_receivables_list_filter(client, db_session):
    _, _, _, _, cus = _seed(db_session)
    _sale_out(client, db_session, cus.id)
    resp = client.get("/api/v1/receivables", params={"partner_id": cus.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["partner_name"] == "北方客户"
    assert Decimal(str(item["amount"])) == Decimal("500")
    assert item["status"] == "open"


def test_settlements_list_with_lines_and_partner(client, db_session):
    """核销单列表：分页，含 lines 与 partner 名称。"""
    _, _, sup, _, cus = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    _sale_out(client, db_session, cus.id)
    ap = db_session.scalar(select(Payable))
    ar = db_session.scalar(select(Receivable))
    assert _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=100).status_code == 200
    assert _settle(client, type_="receipt", partner_id=cus.id,
                   target_type="ar", target_id=ar.id, amount=200).status_code == 200

    resp = client.get("/api/v1/settlements", params={"page": 1, "page_size": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["partner_name"] in ("华东供应商", "北方客户")
    assert len(item["lines"]) == 1


# ---------- 对账单 ----------


def test_statement_supplier_totals_match_manual(client, db_session):
    """供应商对账单：发生/已核销/余额合计与手算一致。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id, qty=100, price=10)  # AP1 1000
    _purchase_in(client, db_session, sup.id, qty=60, price=10)  # AP2 600
    aps = list(db_session.scalars(select(Payable).order_by(Payable.id)))
    assert _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=aps[0].id, amount=400).status_code == 200

    resp = client.get(
        "/api/v1/finance/statement",
        params={"partner_id": sup.id, "type": "supplier",
                "from": "2026-11-01", "to": "2026-11-30"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["partner_name"] == "华东供应商"

    details = body["details"]
    assert len(details) == 2
    assert {d["source_no"] for d in details} != {""}  # 来源单号已带出
    amounts = sorted(Decimal(str(d["amount"])) for d in details)
    assert amounts == [Decimal("600"), Decimal("1000")]
    settled = sorted(Decimal(str(d["settled_amount"])) for d in details)
    assert settled == [Decimal("0"), Decimal("400")]
    balances = sorted(Decimal(str(d["balance"])) for d in details)
    assert balances == [Decimal("600"), Decimal("600")]

    totals = body["totals"]
    assert Decimal(str(totals["total_amount"])) == Decimal("1600")
    assert Decimal(str(totals["total_settled"])) == Decimal("400")
    assert Decimal(str(totals["total_balance"])) == Decimal("1200")

    # 期间收付款：一笔付款 400
    assert len(body["settlements"]) == 1
    assert Decimal(str(body["settlements"][0]["amount"])) == Decimal("400")
    assert Decimal(str(totals["settlement_total"])) == Decimal("400")


def test_statement_period_filter_excludes_outside_docs(client, db_session):
    """对账单期间过滤：期间外的单据与核销不进入明细。"""
    _, _, sup, _, _ = _seed(db_session)
    _purchase_in(client, db_session, sup.id)
    ap = db_session.scalar(select(Payable))
    assert _settle(client, type_="payment", partner_id=sup.id,
                   target_type="ap", target_id=ap.id, amount=100).status_code == 200

    resp = client.get(
        "/api/v1/finance/statement",
        params={"partner_id": sup.id, "type": "supplier",
                "from": "2026-12-01", "to": "2026-12-31"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["details"] == []
    assert body["settlements"] == []
    assert Decimal(str(body["totals"]["total_amount"])) == Decimal("0")

    resp = client.get(
        "/api/v1/finance/statement",
        params={"partner_id": sup.id, "type": "supplier",
                "from": "2026-11-01", "to": "2026-11-30"},
    )
    body = resp.json()
    assert len(body["details"]) == 1
    assert len(body["settlements"]) == 1


def test_statement_customer_type_uses_receivables(client, db_session):
    """客户对账单走应收明细。"""
    _, _, _, _, cus = _seed(db_session)
    _sale_out(client, db_session, cus.id)
    resp = client.get(
        "/api/v1/finance/statement",
        params={"partner_id": cus.id, "type": "customer"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["details"]) == 1
    assert Decimal(str(body["totals"]["total_amount"])) == Decimal("500")
    assert Decimal(str(body["totals"]["total_balance"])) == Decimal("500")
    assert Decimal(str(body["totals"]["settlement_total"])) == Decimal("0")


def test_statement_type_mismatch_with_partner_400(client, db_session):
    """type=supplier 但 partner 是客户 → 400。"""
    _, _, _, _, cus = _seed(db_session)
    resp = client.get(
        "/api/v1/finance/statement",
        params={"partner_id": cus.id, "type": "supplier"},
    )
    assert resp.status_code == 400


def test_statement_nonexistent_partner_404(client, db_session):
    resp = client.get("/api/v1/finance/statement", params={"partner_id": 999, "type": "supplier"})
    assert resp.status_code == 404
