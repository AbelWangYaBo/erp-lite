"""S7 冒烟脚本：打 http://localhost:8080/api/v1/*（走 nginx 反代，一并验证 web 容器）。

完整业务闭环 + 数字断言（任何断言失败：打印失败点、退出码 1）：

  登录 → 建仓库/物料/BOM → PO（RAW 100@10）→ 审核 → 分两次入库 40+60
  → 查库存（100 / 1000 / 均价10）+ AP 1000
  → SO（FG 30@15）→ 审核
  → MRP run（demand FG 50）→ FG make 50 建议转单 → MO 下达 → 领料（100 RAW）
  → 完工入库 50（单位成本 20）→ FG 库存（50/1000/20）
  → SO 出库 30 → FG 库存（20/400/20）+ AR 450
  → MRP run（demand FG 50）→ make 30 / buy 60 建议转采购 → PO 草稿生成
  → 付款核销 AP 部分 200 → 收款核销 AR 450
  → 对账单（供应商 1000/200/800，客户 450/450/0）
  → audit/records 与 audit/logs 留痕检查

说明：SO 出库依赖 FG 现有库存（v1 不允许负库存），故生产完工安排在出库之前；
MRP 需求量取 50（若 demand=20 且 FG 库存=20，净需求为 0，不会产生任何建议）。
"""

import sys
from datetime import date, datetime
from decimal import Decimal

import httpx

BASE = "http://localhost:18080"
API = f"{BASE}/api/v1"

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


def expect(
    label: str, actual, expected, eps: Decimal = Decimal("0.0001")
) -> None:
    try:
        equal = abs(dec(actual) - dec(expected)) <= eps
    except Exception:
        equal = actual == expected
    check(equal, f"{label} = {actual}（期望 {expected}）")


def main() -> int:
    suffix = datetime.now().strftime("%m%d%H%M%S")
    c = httpx.Client(base_url=BASE, timeout=30)

    print("== 1. 登录 ==")
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    check(r.status_code == 200, f"登录 200（实际 {r.status_code} {r.text[:120]}）")
    if r.status_code != 200:
        return finish()
    token = r.json()["token"]
    h = {"Authorization": f"Bearer {token}"}

    # 首页由 web 容器 nginx 托管（验证 web 服务本身）
    r = c.get("/")
    check(r.status_code == 200 and "<div" in r.text, "web 首页 200（nginx 托管前端）")

    def api(method: str, path: str, body=None, **kw) -> dict:
        if body is not None:
            kw["json"] = body
        r = c.request(method, f"{API}{path}", headers=h, **kw)
        if r.status_code >= 400:
            fail(f"{method} {path} -> {r.status_code} {r.text[:200]}")
            raise SystemExit(finish())
        return r.json()

    print("== 2. 基础数据：仓库/往来单位/物料/BOM ==")
    # v1 生产工单只支持单仓（领料/完工均在 MO 仓库），闭环共用一个仓库
    wh1 = api("POST", "/warehouses", json={"code": f"WH{suffix}", "name": "冒烟主仓库"})
    sup = api("POST", "/partners", json={"code": f"S{suffix}", "name": "冒烟供应商", "type": "supplier"})
    cus = api("POST", "/partners", json={"code": f"C{suffix}", "name": "冒烟客户", "type": "customer"})
    raw = api("POST", "/materials", json={
        "code": f"RAW{suffix}", "name": "冒烟螺丝", "spec": "M3×8", "unit": "个",
        "category": "raw", "default_warehouse_id": wh1["id"]})
    fg = api("POST", "/materials", json={
        "code": f"FG{suffix}", "name": "冒烟支架", "spec": "A-01", "unit": "个",
        "category": "finished", "default_warehouse_id": wh1["id"]})
    bom = api("PUT", f"/boms/{fg['id']}", json={
        "lines": [{"material_id": raw["id"], "qty_per": 2, "loss_pct": 0}]})
    check(len(bom["lines"]) == 1, "FG BOM 保存（1 行，2×RAW）")

    print("== 3. 采购：PO 100@10 → 审核 → 分两次入库 40+60 ==")
    po = api("POST", "/purchase-orders", json={
        "partner_id": sup["id"], "order_date": str(date.today()),
        "lines": [{"material_id": raw["id"], "qty": 100, "unit_price": 10,
                   "warehouse_id": wh1["id"]}]})
    check(po["status"] == "draft", f"PO {po['doc_no']} 草稿")
    line_id = po["lines"][0]["id"]
    po = api("POST", f"/purchase-orders/{po['id']}/approve")
    check(po["status"] == "approved", "PO 审核通过")
    po = api("POST", f"/purchase-orders/{po['id']}/receive", [
        {"line_id": line_id, "qty": 40, "warehouse_id": wh1["id"]}])
    check(po["status"] == "partial", "第一次入库 40 → 部分入库")
    po = api("POST", f"/purchase-orders/{po['id']}/receive", [
        {"line_id": line_id, "qty": 60, "warehouse_id": wh1["id"]}])
    check(po["status"] == "completed", "第二次入库 60 → 全部入库")

    print("== 4. 库存断言：RAW (100, 1000, 均价10) + AP 1000 ==")
    stock = {s["material_id"]: s for s in api("GET", "/inventory/stock")}
    expect("RAW 现存量", stock[raw["id"]]["qty"], 100)
    expect("RAW 金额", stock[raw["id"]]["amount"], 1000)
    expect("RAW 移动加权均价", stock[raw["id"]]["avg_cost"], 10)
    aps = api("GET", "/payables", params={"partner_id": sup["id"]})
    expect("应付发生额合计", sum(dec(p["amount"]) for p in aps["items"]), 1000)
    expect("应付未核销合计", sum(dec(p["settled_amount"]) for p in aps["items"]), 0)
    ap_open = aps["items"][0]  # 单据按收货批次生成（400 + 600），取首张做部分核销

    print("== 5. 销售：SO FG 30@15 → 审核 ==")
    so = api("POST", "/sales-orders", json={
        "partner_id": cus["id"], "order_date": str(date.today()),
        "lines": [{"material_id": fg["id"], "qty": 30, "unit_price": 15,
                   "warehouse_id": wh1["id"]}]})
    so = api("POST", f"/sales-orders/{so['id']}/approve")
    check(so["status"] == "approved", f"SO {so['doc_no']} 审核通过（待出库）")
    so_line = so["lines"][0]["id"]

    print("== 6. MRP run（demand FG 50）→ make 建议转单 → MO 下达/领料/完工 ==")
    run1 = api("POST", "/mrp/run", json={
        "demand": [{"material_id": fg["id"], "qty": 50}]})
    sugs = {(s["material_id"], s["type"]): dec(s["net_qty"]) for s in run1["suggestions"]}
    expect("MRP#1 FG make 净需求", sugs.get((fg["id"], "make"), 0), 50)
    fg_sug = next(s for s in run1["suggestions"] if s["type"] == "make")
    conv = api("POST", "/mrp/suggestions/convert", json={"ids": [fg_sug["id"]]})
    mo_id = conv["converted"][0]["doc_id"]
    mo = api("GET", f"/manufacturing-orders/{mo_id}")
    check(mo["status"] == "draft" and dec(mo["planned_qty"]) == 50,
          f"建议转生产 → MO {mo['doc_no']} 草稿 计划 50")
    mo = api("POST", f"/manufacturing-orders/{mo_id}/approve")
    check(mo["status"] == "approved", "MO 下达")
    mo = api("POST", f"/manufacturing-orders/{mo_id}/issue", json={})
    detail = api("GET", f"/manufacturing-orders/{mo_id}")
    expect("领料成本合计（100 RAW @10）", detail["issued_qty_cost"], 1000)
    mo = api("POST", f"/manufacturing-orders/{mo_id}/receive",
             json={"qty": 50, "extra_cost": 0})
    check(mo["status"] == "completed", "完工入库 50 → 全部完工")

    print("== 7. 成品库存断言：FG (50, 1000, 均价20)，RAW 清零 ==")
    stock = {s["material_id"]: s for s in api("GET", "/inventory/stock")}
    expect("FG 现存量", stock[fg["id"]]["qty"], 50)
    expect("FG 金额", stock[fg["id"]]["amount"], 1000)
    expect("FG 完工单位成本", stock[fg["id"]]["avg_cost"], 20)
    expect("RAW 现存量（领料后）", stock.get(raw["id"], {"qty": 0})["qty"], 0)

    print("== 8. SO 出库 30 → FG 库存 (20, 400, 20) + AR 450 ==")
    so = api("POST", f"/sales-orders/{so['id']}/ship", [
        {"line_id": so_line, "qty": 30, "warehouse_id": wh1["id"]}])
    check(so["status"] == "completed", "SO 出库 30 → 全部出库")
    stock = {s["material_id"]: s for s in api("GET", "/inventory/stock")}
    expect("FG 出库后现存量", stock[fg["id"]]["qty"], 20)
    expect("FG 出库后金额（出库成本=移动加权 20）", stock[fg["id"]]["amount"], 400)
    expect("FG 出库后均价", stock[fg["id"]]["avg_cost"], 20)
    ars = api("GET", "/receivables", params={"partner_id": cus["id"]})
    expect("应收发生额", sum(dec(a["amount"]) for a in ars["items"]), 450)
    ar = ars["items"][0]

    print("== 9. MRP run（demand FG 50）→ make 30 / buy 60 → 建议转采购 ==")
    run2 = api("POST", "/mrp/run", json={
        "demand": [{"material_id": fg["id"], "qty": 50}]})
    sugs2 = {(s["material_id"], s["type"]): dec(s["net_qty"])
             for s in run2["suggestions"]}
    expect("MRP#2 FG make 净需求（扣库存 20）", sugs2.get((fg["id"], "make"), 0), 30)
    expect("MRP#2 RAW buy 净需求（30×2，扣库存 0）", sugs2.get((raw["id"], "buy"), 0), 60)
    buy_sug = next(s for s in run2["suggestions"] if s["type"] == "buy")
    conv2 = api("POST", "/mrp/suggestions/convert",
                json={"ids": [buy_sug["id"]], "partner_id": sup["id"]})
    po2_id = conv2["converted"][0]["doc_id"]
    po2 = api("GET", f"/purchase-orders/{po2_id}")
    check(po2["status"] == "draft" and dec(po2["lines"][0]["qty"]) == 60,
          "MRP 建议转采购 → PO 草稿 RAW 60")

    print("== 10. 付款核销 AP 部分 200 / 收款核销 AR 450 ==")
    st = api("POST", "/settlements", json={
        "type": "payment", "partner_id": sup["id"], "date": str(date.today()),
        "remark": "冒烟-部分付款",
        "lines": [{"target_type": "ap", "target_id": ap_open["id"], "amount": 200}]})
    expect("付款核销单金额", st["amount"], 200)
    ap_after = api("GET", "/payables", params={"partner_id": sup["id"]})
    ap_row = next(p for p in ap_after["items"] if p["id"] == ap_open["id"])
    expect("AP 已核销", ap_row["settled_amount"], 200)
    check(ap_row["status"] == "partial", "AP 状态 partial")
    st2 = api("POST", "/settlements", json={
        "type": "receipt", "partner_id": cus["id"], "date": str(date.today()),
        "remark": "冒烟-全额收款",
        "lines": [{"target_type": "ar", "target_id": ar["id"], "amount": 450}]})
    ar_after = api("GET", "/receivables", params={"partner_id": cus["id"]})
    ar_row = next(a for a in ar_after["items"] if a["id"] == ar["id"])
    expect("AR 已核销", ar_row["settled_amount"], 450)
    check(ar_row["status"] == "settled", "AR 状态 settled")

    print("== 11. 对账单 ==")
    s_sup = api("GET", "/finance/statement",
                params={"partner_id": sup["id"], "type": "supplier"})
    expect("供应商对账单-发生", s_sup["totals"]["total_amount"], 1000)
    expect("供应商对账单-已核销", s_sup["totals"]["total_settled"], 200)
    expect("供应商对账单-余额", s_sup["totals"]["total_balance"], 800)
    expect("供应商对账单-付款合计", s_sup["totals"]["settlement_total"], 200)
    s_cus = api("GET", "/finance/statement",
                params={"partner_id": cus["id"], "type": "customer"})
    expect("客户对账单-发生", s_cus["totals"]["total_amount"], 450)
    expect("客户对账单-已核销", s_cus["totals"]["total_settled"], 450)
    expect("客户对账单-余额", s_cus["totals"]["total_balance"], 0)

    print("== 12. 审计留痕 ==")
    rec = api("GET", "/audit/records", params={"page_size": 5})
    logs = api("GET", "/audit/logs", params={"page_size": 5})
    check(rec["total"] > 0, f"audit/records 留痕 {rec['total']} 条")
    check(logs["total"] > 0, f"audit/logs 留痕 {logs['total']} 条")
    po_detail = api("GET", f"/purchase-orders/{po['id']}")
    actions = {a["action"] for a in po_detail["audits"]}
    check({"create", "status"} <= actions,
          f"PO 状态流转留痕（audits 含 create/status：{sorted(actions)}）")
    move_page = api("GET", "/inventory/moves", params={"material_id": raw["id"]})
    types = {m["move_type"] for m in move_page["items"]}
    check("purchase_in" in types and "mo_issue" in types,
          f"库存台账流水留痕（{sorted(types)}）")

    return finish()


def finish() -> int:
    print(f"\n==== 冒烟结果：{len(passed)} 项通过，{len(failures)} 项失败 ====")
    if failures:
        print("失败点：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
