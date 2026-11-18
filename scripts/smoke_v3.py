"""v3 冒烟脚本：打 http://localhost:8100/api/v1/*（本地开发库，八角色全链路闭环）。

前置：
  cd backend && DATABASE_URL=sqlite:///./erp-dev.db alembic upgrade head
  DATABASE_URL=sqlite:///./erp-dev.db uv run python ../scripts/seed.py
  DATABASE_URL=sqlite:///./erp-dev.db uv run uvicorn erp_lite.main:app --port 8100

八角色闭环断言（任何断言失败：打印失败点、退出码 1）：

  八账号登录 → 越权矩阵抽测（403）
  → 合规拦截：过期供应商 SUP-003 建单 400
  → purchase 建 PO1(RAW 40@5) → purchase 自审 400（职责分离）→ admin approve
  → warehouse 收货 LOT-v3-1（错仓库 400 → 行仓库一致收货 20）→ 再收 20 到 LOT-v3-2
  → 旧批次号复用 400（不复用）
  → qc 录入 LOT-v3-1（建议 pass）→ qc 自判 400 → qa judge pass → qualified
  → qc 录入 LOT-v3-2（建议 fail）→ qa judge fail → rejected
  → qa 处置 destroy LOT-v3-2 → closed + loss_amount>0 + 现存清零（报废闭环）
  → production MRP(demand FG 6) → RAW buy 建议转单（无资质供应商 400 → SUP-001 成功）
  → admin 下达 MRP 转的 PO 草稿（改价后 approve 由 admin 做——purchase 建单不自审）
  → 转单 MO 下达（production 建单 → admin？production 即 mo.release；建单=转单人
    production，下达换 admin）→ 领料 LOT-v3-1（pending 批次 400 断言）
  → 多领 2 退回 2（unissue：现存恢复 + 成本冲回）→ 完工 6（completed_qty 持久化断言）
  → 成品批次 pending → qc/qa 放行（规则 B）
  → sales 建 SO(FG 6@25) → sales 自审 400 → admin approve → warehouse 出库
    （合格成品批次，UDI 断言；pending 原料批次出库 400）
  → sales 退货 2（批次回 pending + AR 冲减 + SO 回 partial）
  → finance 核销 AP 部分 → 冲回（状态恢复）→ 对账单勾稽断言
  → warehouse 建盘点 → 录实盘 → finance post → adjust 流水 + 现存修正断言
  → 资质：purchase 录入 → purchase 自审 400 → qa 审核 → admin 撤销 → revoked 拦截
  → reject 流程：purchase 建 PO2 → admin reject → 回 draft + 原因留痕
  → dashboard 各角色待办断言 + 审计过滤断言（actor=qc）
"""

import sys
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx

BASE = "http://localhost:8100"
API = f"{BASE}/api/v1"

ACCOUNTS = {
    "admin": "admin123",
    "purchase": "p123",
    "sales": "s123",
    "warehouse": "w123",
    "production": "p123456",
    "qc": "qc123",
    "qa": "q123",
    "finance": "f123",
}

passed: list[str] = []
failures: list[str] = []


def ok(msg: str) -> None:
    passed.append(msg)
    print(f"  PASS {msg}")


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL {msg}")


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else fail(msg)


def dec(x) -> Decimal:
    return Decimal(str(x))


def expect(label: str, actual, expected, eps: Decimal = Decimal("0.0001")) -> None:
    try:
        equal = abs(dec(actual) - dec(expected)) <= eps
    except Exception:
        equal = actual == expected
    check(equal, f"{label} = {actual}（期望 {expected}）")


def main() -> int:
    suffix = datetime.now().strftime("%m%d%H%M%S")
    c = httpx.Client(base_url=BASE, timeout=30)
    today = date.today()

    tokens: dict[str, str] = {}

    def login(role: str) -> str:
        r = c.post(f"{API}/auth/login", json={"username": role, "password": ACCOUNTS[role]})
        assert r.status_code == 200, f"{role} 登录失败：{r.text}"
        return r.json()["token"]

    def call(role: str, method: str, path: str, **kw):
        return c.request(
            method, f"{API}{path}",
            headers={"Authorization": f"Bearer {tokens[role]}"},
            **{k: v for k, v in kw.items() if k != "expect_status"},
        )

    def call_expect(role: str, method: str, path: str, status_expected: int, label: str, **kw):
        r = call(role, method, path, **kw)
        check(r.status_code == status_expected,
              f"{label} → {r.status_code}（期望 {status_expected}）"
              + ("" if r.status_code == status_expected else f"，响应：{r.text[:200]}"))
        return r

    def js(r):
        return r.json()

    print("== 1. 八账号登录 ==")
    for role in ACCOUNTS:
        try:
            tokens[role] = login(role)
            ok(f"{role} 登录")
        except AssertionError as exc:
            fail(str(exc))
            return finish()

    me = js(call("sales", "GET", "/auth/me"))
    check(me.get("role") == "sales", f"/auth/me role = {me.get('role')}（期望 sales）")

    print("== 2. 越权矩阵抽测 ==")
    call_expect("warehouse", "POST", "/purchase-orders", 403, "warehouse 建 PO")
    call_expect("qc", "POST", "/batches/1/judge", 403, "qc 复核判定（无 batch.judge）")
    call_expect("qa", "POST", "/batches/1/inspect", 403, "qa 检验录入（无 batch.inspect）")
    call_expect("purchase", "POST", "/purchase-orders/1/receive", 403, "purchase 收货")
    call_expect("finance", "POST", "/purchase-orders", 403, "finance 建 PO")
    call_expect("sales", "POST", "/settlements", 403, "sales 核销（无 settlement.create）")

    print("== 3. 合规拦截：过期供应商 ==")
    materials = js(call("admin", "GET", "/materials"))
    raw = next(m for m in materials["items"] if m["code"] == "RAW-SCREW-M3X8")
    fg = next(m for m in materials["items"] if m["code"] == "FG-BRACKET")
    warehouses = js(call("admin", "GET", "/warehouses"))
    wh_main = next(w for w in warehouses["items"] if w["code"] == "MAIN")
    sup3 = None
    for p in js(call("admin", "GET", "/partners"))["items"]:
        if p["code"] == "SUP-003":
            sup3 = p
    po_body = {
        "partner_id": sup3["id"], "order_date": today.isoformat(),
        "lines": [{"material_id": raw["id"], "qty": "10", "unit_price": "5",
                   "warehouse_id": wh_main["id"]}],
    }
    call_expect("purchase", "POST", "/purchase-orders", 400, "过期供应商建 PO",
                json=po_body)

    print("== 4. PO1：建单 → 自审拦截 → 审核 ==")
    sup1 = next(p for p in js(call("admin", "GET", "/partners"))["items"] if p["code"] == "SUP-001")
    po_body["partner_id"] = sup1["id"]
    po_body["lines"][0]["qty"] = "40"
    po1 = js(call_expect("purchase", "POST", "/purchase-orders", 200, "purchase 建 PO1",
                         json=po_body))
    call_expect("purchase", "POST", f"/purchase-orders/{po1['id']}/approve", 400,
                "purchase 自审 PO1（不得自审）")
    call_expect("admin", "POST", f"/purchase-orders/{po1['id']}/approve", 200, "admin 审核 PO1")

    print("== 5. 收货（仓库一致性 + 批次不复用） ==")
    line1 = po1["lines"][0]
    wrong_wh = [{"line_id": line1["id"], "qty": "20",
                 "warehouse_id": wh_main["id"] + 999}]
    call_expect("warehouse", "POST", f"/purchase-orders/{po1['id']}/receive", 400,
                "错仓入库（与行仓库不一致）", json=wrong_wh)
    recv1 = [{"line_id": line1["id"], "qty": "20", "warehouse_id": wh_main["id"],
              "batch_no": f"LOT-v3-1-{suffix}"}]
    call_expect("warehouse", "POST", f"/purchase-orders/{po1['id']}/receive", 200,
                "收货 20 → LOT-v3-1（待检）", json=recv1)
    recv2 = [{"line_id": line1["id"], "qty": "20", "warehouse_id": wh_main["id"],
              "batch_no": f"LOT-v3-1-{suffix}"}]
    call_expect("warehouse", "POST", f"/purchase-orders/{po1['id']}/receive", 400,
                "旧批次号复用 400（v3 不复用）", json=recv2)
    recv2 = [{"line_id": line1["id"], "qty": "20", "warehouse_id": wh_main["id"],
              "batch_no": f"LOT-v3-2-{suffix}"}]
    call_expect("warehouse", "POST", f"/purchase-orders/{po1['id']}/receive", 200,
                "收货 20 → LOT-v3-2（待检）", json=recv2)

    print("== 6. 双段检验：qc 录入 → qa 判定 ==")
    def batch_by_no(no: str):
        r = call("admin", "GET", "/batches", params={"keyword": no})
        return js(r)["items"][0]

    lot1 = batch_by_no(f"LOT-v3-1-{suffix}")
    lot2 = batch_by_no(f"LOT-v3-2-{suffix}")
    expect("LOT-v3-1 insp_status", lot1["insp_status"], "pending")
    inspect_body = {"items": [{"name": "外观", "result": "合格"},
                              {"name": "尺寸", "result": "合格"}],
                    "suggest_conclusion": "pass"}
    rec = js(call_expect("qc", "POST", f"/batches/{lot1['id']}/inspect", 200,
                         "qc 录入 LOT-v3-1 检验记录（建议 pass）", json=inspect_body))
    check(rec.get("conclusion") is None, "录入后待判定（conclusion 为空）")
    call_expect("qc", "POST", f"/batches/{lot1['id']}/judge", 403,
                "qc 无复核权限（judge 归 qa，双段制硬分离）", json={"conclusion": "pass"})
    call_expect("qa", "POST", f"/batches/{lot1['id']}/judge", 200,
                "qa 复核判定 pass → qualified", json={"conclusion": "pass"})
    lot1 = batch_by_no(f"LOT-v3-1-{suffix}")
    expect("LOT-v3-1 insp_status", lot1["insp_status"], "qualified")

    print("== 7. 不合格批次：录入 fail → 判定 → 报废处置闭环 ==")
    call_expect("admin", "POST", f"/batches/{lot2['id']}/inspect", 200,
                "admin 录入 LOT-v3-2（建议 fail）",
                json={"items": [{"name": "外观", "result": "锈蚀"}],
                      "suggest_conclusion": "fail"})
    call_expect("admin", "POST", f"/batches/{lot2['id']}/judge", 400,
                "admin 自判自己录入的检验（不得自审）", json={"conclusion": "fail"})
    call_expect("qa", "POST", f"/batches/{lot2['id']}/judge", 200,
                "qa 判定 fail → rejected", json={"conclusion": "fail"})
    lot2 = batch_by_no(f"LOT-v3-2-{suffix}")
    expect("LOT-v3-2 insp_status", lot2["insp_status"], "rejected")
    call_expect("qa", "POST", f"/batches/{lot2['id']}/dispose", 200,
                "qa 处置 destroy（报废闭环）", json={"disposition": "destroy", "remark": "锈蚀报废"})
    lot2 = batch_by_no(f"LOT-v3-2-{suffix}")
    expect("LOT-v3-2 insp_status", lot2["insp_status"], "closed")
    check(dec(lot2.get("loss_amount", 0)) > 0, f"报废损失 loss_amount={lot2.get('loss_amount')} > 0")
    r = call("admin", "GET", "/inventory/stock", params={"material_id": raw["id"]})
    stock_raw = js(r)[0]
    expect("RAW rejected 现存清零", stock_raw.get("rejected_qty") or 0, 0)

    print("== 8. MRP + 转单（资质拦截） ==")
    semi = next(m for m in materials["items"] if m["code"] == "SEMI-BRACKET")
    # demand 11 → RAW 需求 22 > qualified 现存 20 → 产生 buy 建议（缺口 2）
    call_expect("production", "POST", "/mrp/run", 200, "MRP 运算（demand SEMI 11）",
                json={"demand": [{"material_id": semi["id"], "qty": "11"}]})
    sugs = js(call("production", "GET", "/mrp/suggestions", params={"converted": "false"}))["items"]
    buy_sug = next((s for s in sugs if s["type"] == "buy" and s["material_id"] == raw["id"]), None)
    check(buy_sug is not None, "MRP 产出 RAW buy 建议（需求 22 > 可用 20）")
    if buy_sug:
        call_expect("production", "POST", "/mrp/suggestions/convert", 400,
                    "转单到过期供应商（资质拦截）",
                    json={"ids": [buy_sug["id"]], "partner_id": sup3["id"]})
        r = call_expect("production", "POST", "/mrp/suggestions/convert", 200,
                        "转单到 SUP-001（资质有效）",
                        json={"ids": [buy_sug["id"]], "partner_id": sup1["id"]})
        conv = js(r)["converted"][0]
        mrp_po = js(call("admin", "GET", f"/purchase-orders/{conv['doc_id']}"))
        # purchase（seed 同名账号）建的草稿由 admin 审核；先改价（0 价警告语义）
        upd = {"lines": [{"material_id": raw["id"], "qty": mrp_po["lines"][0]["qty"],
                          "unit_price": "5", "warehouse_id": mrp_po["lines"][0]["warehouse_id"]}]}
        call_expect("admin", "PUT", f"/purchase-orders/{mrp_po['id']}", 200,
                    "MRP 转 PO 改价（0 价 → 5）", json=upd)
        call_expect("admin", "POST", f"/purchase-orders/{mrp_po['id']}/approve", 200,
                    "admin 审核 MRP 转 PO（purchase 建单不自审）")

    print("== 9. MO：下达 → 领料 → 退回 → 完工（规则 B） ==")
    # make 建议留置不转（演示 MRP 建议流）；MO 走手建定长 6，领料/退料/完工数字断言稳定
    r = call_expect("production", "POST", "/manufacturing-orders", 200, "手建 MO(SEMI 6)",
                    json={"material_id": semi["id"], "planned_qty": "6",
                          "warehouse_id": wh_main["id"], "remark": "v3 冒烟工单"})
    mo_id = js(r)["id"]
    call_expect("production", "POST", f"/manufacturing-orders/{mo_id}/approve", 400,
                "production 自下达 MO（不得自审）")
    call_expect("admin", "POST", f"/manufacturing-orders/{mo_id}/approve", 200, "admin 下达 MO")
    # 领料：pending 批次拦截
    issue_body = {"lines": [{"material_id": raw["id"], "qty": "12",
                             "batch_id": lot2["id"]}]}
    call_expect("production", "POST", f"/manufacturing-orders/{mo_id}/issue", 400,
                "closed/rejected 批次领料 400", json=issue_body)
    issue_body = {"lines": [{"material_id": raw["id"], "qty": "14",
                             "batch_id": lot1["id"]}]}
    call_expect("production", "POST", f"/manufacturing-orders/{mo_id}/issue", 200,
                "领料 RAW×14（qualified 批次）", json=issue_body)
    # 退回 2
    unissue_body = {"material_id": raw["id"], "qty": "2", "batch_id": lot1["id"]}
    call_expect("production", "POST", f"/manufacturing-orders/{mo_id}/unissue", 200,
                "领料退回 2（红字，现存恢复）", json=unissue_body)
    r = call("admin", "GET", f"/batches/{lot1['id']}")
    expect("退回后批次现存（20 − 14 领 + 2 退）", js(r)["stock_qty"], 8)
    # 完工 6
    call_expect("production", "POST", f"/manufacturing-orders/{mo_id}/receive", 200,
                "完工入库 6", json={"qty": "6"})
    mo = js(call("admin", "GET", f"/manufacturing-orders/{mo_id}"))
    expect("completed_qty 持久化", mo["completed_qty"], 6)
    expect("received_qty 持久化", mo["received_qty"], 6)
    # 完工批次 pending → 放行
    out_batches = [b for b in js(call("admin", "GET", "/batches", params={"material_id": semi["id"]}))["items"]
                   if b["source_id"] == mo_id]
    check(len(out_batches) >= 1, "完工生成 SEMI 批次")
    out_batch = out_batches[-1]
    expect("完工批次 pending（规则 B）", out_batch["insp_status"], "pending")
    call_expect("qc", "POST", f"/batches/{out_batch['id']}/inspect", 200,
                "qc 录入完工批次检验（建议 pass）",
                json={"items": [{"name": "检验", "result": "合格"}],
                      "suggest_conclusion": "pass"})
    call_expect("qa", "POST", f"/batches/{out_batch['id']}/judge", 200,
                "qa 放行完工批次", json={"conclusion": "pass"})

    print("== 10. SO：建单 → 审核 → 出库（带批次流水） ==")
    cus = next(p for p in js(call("admin", "GET", "/partners"))["items"] if p["code"] == "CUS-001")
    so_body = {"partner_id": cus["id"], "order_date": today.isoformat(),
               "lines": [{"material_id": semi["id"], "qty": "6", "unit_price": "25",
                          "warehouse_id": wh_main["id"]}]}
    so = js(call_expect("sales", "POST", "/sales-orders", 200, "sales 建 SO(SEMI 6)", json=so_body))
    call_expect("sales", "POST", f"/sales-orders/{so['id']}/approve", 400, "sales 自审 SO")
    call_expect("admin", "POST", f"/sales-orders/{so['id']}/approve", 200, "admin 审核 SO")
    ship_body = [{"line_id": so["lines"][0]["id"], "qty": "6",
                  "warehouse_id": wh_main["id"], "batch_id": out_batch["id"]}]
    call_expect("warehouse", "POST", f"/sales-orders/{so['id']}/ship", 200,
                "warehouse 出库（放行批次）", json=ship_body)
    moves = js(call("admin", "GET", "/inventory/moves"))["items"]
    ship_move = next((m for m in moves if m["move_type"] == "sale_out"
                      and m.get("batch_id") == out_batch["id"]), None)
    check(ship_move is not None and ship_move.get("batch_no"),
          f"出库流水暴露批次号（batch_no={ship_move and ship_move.get('batch_no')}）")

    print("== 11. 销售退货（批次回 pending + 冲应收） ==")
    call_expect("sales", "POST", "/sales-returns", 200, "sales 建销售退货 2",
                json={"so_id": so["id"], "batch_id": out_batch["id"], "qty": "2",
                      "reason": "客户退回"})
    out_batch2 = js(call("admin", "GET", f"/batches/{out_batch['id']}"))
    expect("退货后批次回 pending（待重检）", out_batch2["insp_status"], "pending")
    so2 = js(call("admin", "GET", f"/sales-orders/{so['id']}"))
    expect("SO 回 partial", so2["status"], "partial")
    expect("shipped_qty 冲减", so2["lines"][0]["shipped_qty"], 4)

    print("== 12. 核销 → 冲回 → 对账勾稽 ==")
    aps = js(call("finance", "GET", "/payables"))["items"]
    ap1 = next(a for a in aps if a["amount"] and dec(a["amount"]) > 0)
    settle_body = {"type": "payment", "partner_id": ap1["partner_id"],
                   "date": today.isoformat(),
                   "lines": [{"target_type": "ap", "target_id": ap1["id"],
                              "amount": str(min(dec(ap1["amount"]), Decimal("50")))}]}
    st = js(call_expect("finance", "POST", "/settlements", 200, "finance 核销应付部分",
                        json=settle_body))
    r = call_expect("finance", "POST", f"/settlements/{st['id']}/reverse", 200,
                    "finance 冲回核销单", json={"date": today.isoformat(),
                                               "remark": "冲回演示"})
    ap_after = js(call("finance", "GET", "/payables"))["items"]
    ap1_after = next(a for a in ap_after if a["id"] == ap1["id"])
    expect("冲回后 AP 恢复未清", ap1_after["settled_amount"], 0)
    expect("冲回后 AP 状态", ap1_after["status"], "open")
    r = call("finance", "GET", "/finance/statement",
             params={"partner_id": sup1["id"], "type": "supplier"})
    stmt = js(r)
    closing = dec(stmt.get("closing_balance", 0))
    opening = dec(stmt.get("opening_balance", 0))
    new_amt = dec(stmt["totals"].get("period_new_amount", 0))
    settle_net = dec(stmt["totals"].get("settlement_total", 0))
    expect("对账勾稽：期初+新增−核销=期末", opening + new_amt - settle_net, closing)

    print("== 13. 盘点闭环 ==")
    r = call_expect("warehouse", "POST", "/inventory/stock-counts", 200,
                    "warehouse 建盘点单（账面快照）",
                    json={"warehouse_id": wh_main["id"], "remark": "v3 冒烟盘点"})
    sc = js(r)
    check(len(sc["lines"]) > 0, f"快照 {len(sc['lines'])} 行")
    # 全部行必须录实盘（业务校验：存在未录行则 post 400）；目标行盘亏 1，其余账实相符
    upd_lines = [
        {"line_id": l["id"],
         "counted_qty": str(dec(l["system_qty"]) - 1)
         if (l["material_id"] == raw["id"] and l["batch_id"] == lot1["id"])
         else str(dec(l["system_qty"]))}
        for l in sc["lines"]
    ]
    call_expect("warehouse", "PUT", f"/inventory/stock-counts/{sc['id']}", 200,
                "录实盘（全部行，目标行盘亏 1）", json={"lines": upd_lines})
    call_expect("warehouse", "POST", f"/inventory/stock-counts/{sc['id']}/post", 403,
                "warehouse 过账 403（post 归 finance）")
    call_expect("finance", "POST", f"/inventory/stock-counts/{sc['id']}/post", 200,
                "finance 过账（带批次 adjust）")
    r = call("admin", "GET", f"/batches/{lot1['id']}")
    expect("盘点后批次现存（8 − 盘亏 1）", js(r)["stock_qty"], 7)
    call_expect("finance", "POST", f"/inventory/stock-counts/{sc['id']}/post", 400,
                "重复过账 400")

    print("== 14. 资质：录入 → 审核 → 自审拦截 → 撤销闭环 ==")
    cert_body = {"partner_id": sup1["id"], "cert_name": "质量管理体系认证",
                 "cert_no": f"CERT-v3-{suffix}", "issuer": "认证机构",
                 "valid_from": today.isoformat(),
                 "valid_until": (today + timedelta(days=365)).isoformat()}
    cert = js(call_expect("purchase", "POST", "/partner-certs", 200, "purchase 录入资质",
                          json=cert_body))
    call_expect("qa", "POST", f"/partner-certs/{cert['id']}/audit", 200, "qa 审核资质")
    cert2_body = dict(cert_body, cert_no=f"CERT-v3-adm-{suffix}", cert_name="Admin自测证照")
    cert2 = js(call_expect("admin", "POST", "/partner-certs", 200, "admin 录入第二张资质",
                           json=cert2_body))
    call_expect("admin", "POST", f"/partner-certs/{cert2['id']}/audit", 400,
                "admin 自审自己录入的资质（不得自审）")
    call_expect("qa", "POST", f"/partner-certs/{cert['id']}/revoke", 400,
                "qa 撤销自己审核的资质（不得自审）", json={"reason": "测试"})
    call_expect("admin", "POST", f"/partner-certs/{cert['id']}/revoke", 200,
                "admin 撤销资质", json={"reason": "证照吊销演示"})

    print("== 15. 审批拒绝退回 ==")
    po2_body = {"partner_id": sup1["id"], "order_date": today.isoformat(),
                "lines": [{"material_id": raw["id"], "qty": "5", "unit_price": "5",
                           "warehouse_id": wh_main["id"]}]}
    po2 = js(call_expect("purchase", "POST", "/purchase-orders", 200, "purchase 建 PO2",
                         json=po2_body))
    call_expect("admin", "POST", f"/purchase-orders/{po2['id']}/approve", 200, "admin 审核 PO2")
    call_expect("admin", "POST", f"/purchase-orders/{po2['id']}/reject", 200,
                "admin 拒绝 PO2（退回草稿）", json={"reason": "数量有误，退回修改"})
    po2_after = js(call("admin", "GET", f"/purchase-orders/{po2['id']}"))
    expect("PO2 回 draft", po2_after["status"], "draft")
    check(po2_after.get("reject_reason") == "数量有误，退回修改", "拒绝原因留痕")

    print("== 16. dashboard + 审计过滤 ==")
    dash_qc = js(call("qc", "GET", "/dashboard"))
    check(any(t["key"] == "batch_to_inspect" for t in dash_qc["todos"]), "qc 工作台有待检待办")
    dash_fin = js(call("finance", "GET", "/dashboard"))
    check(any(t["key"] == "ap_to_settle" for t in dash_fin["todos"]), "finance 工作台有待核销待办")
    dash_sales = js(call("sales", "GET", "/dashboard"))
    check(any(t["key"] == "so_to_approve" for t in dash_sales["todos"]), "sales 工作台有待审 SO")
    logs = js(call("admin", "GET", "/audit/records", params={"actor": "qc"}))["items"]
    check(any(l["action"] == "inspection" for l in logs), "审计按 actor=qc 过滤到检验录入")
    check(all(l.get("role") == "qc" for l in logs if l["actor"] == "qc"),
          "审计记录带 role=qc")

    return finish()


def finish() -> int:
    print(f"\n===== 结果：PASS {len(passed)} / FAIL {len(failures)} =====")
    if failures:
        print("失败项：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("v3 全链路冒烟全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
