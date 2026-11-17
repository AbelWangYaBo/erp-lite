"""v2 冒烟脚本：打 http://localhost:8100/api/v1/*（本地开发库，五角色全链路）。

前置：cd backend && DATABASE_URL=sqlite:///./erp-dev.db alembic upgrade head
      DATABASE_URL=sqlite:///./erp-dev.db uv run python ../scripts/seed.py
      DATABASE_URL=sqlite:///./erp-dev.db uv run uvicorn erp_lite.main:app --port 8100

完整 GMP 演示链 + 数字断言（任何断言失败：打印失败点、退出码 1）：

  五账号登录（admin/purchase/warehouse/production/qa）
  → purchase 建 PO（供应商 SUP-001 有效资质，RAW 40@5）→ approve
  → 越权抽测：warehouse 建 PO 403 / production 判检验 403 / purchase 入库 403 / qa 建 PO 403
  → 合规拦截：过期供应商 SUP-003 建单 400
  → warehouse 分两次入库（LOT-acc-1 / LOT-acc-2，各 20）
  → qa 查待检批次 → LOT-acc-1 判定 pass（LOT-acc-2 保持 pending）
  → production MRP（demand FG 10）→ make FG 10 / make SEMI 10（qualified RAW 20 恰好抵扣）
  → 转单 MO1(SEMI) 下达 → 领料（合格批次 LOT-acc-1）→ 完工 10（SEMI 批次自动合格）
  → 转单 MO2(FG) 下达 → 领料（SEMI 批次）→ 完工 10（FG 批次自动，udi_di 挂物料）
  → 合规拦截：pending 批次 LOT-acc-2 领料 400（MO3 试领后关闭）
  → purchase 建 SO（FG 10@25）→ approve → warehouse 出库（合格成品批次，UDI 信息校验）
  → admin 付款核销 AP 部分 80 + 收款核销 AR 250 → 对账单断言
  → 追溯：FG 批次 trace-forward 查到 SEMI/RAW 批次+供应商；RAW 批次 trace-backward 查到 SEMI/FG 批次
  → 资质录入（purchase）→ 资质审核（qa）→ audit/records 留痕（inspection / partner_cert audit）
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
    "warehouse": "w123",
    "production": "p123456",
    "qa": "q123",
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

    tokens: dict[str, str] = {}

    def login(role: str) -> str:
        r = c.post(f"{API}/auth/login", json={
            "username": role, "password": ACCOUNTS[role]})
        if r.status_code != 200:
            fail(f"{role} 登录 -> {r.status_code} {r.text[:120]}")
            raise SystemExit(finish())
        return r.json()["token"]

    def api(role: str, method: str, path: str, body=None, **kw) -> dict:
        if body is not None:
            kw["json"] = body
        r = c.request(method, f"{API}{path}",
                      headers={"Authorization": f"Bearer {tokens[role]}"}, **kw)
        if r.status_code >= 400:
            fail(f"[{role}] {method} {path} -> {r.status_code} {r.text[:200]}")
            raise SystemExit(finish())
        return r.json()

    def api_expect(role: str, method: str, path: str, want: int, body=None) -> dict:
        """期望失败（403/400）的请求：状态码不符即 FAIL，不符时中止。"""
        kw = {"json": body} if body is not None else {}
        r = c.request(method, f"{API}{path}",
                      headers={"Authorization": f"Bearer {tokens[role]}"}, **kw)
        check(r.status_code == want,
              f"[{role}] {method} {path} -> {r.status_code}（期望 {want} {r.text[:80]}）")
        if r.status_code != want:
            raise SystemExit(finish())
        try:
            return r.json()
        except Exception:
            return {}

    print("== 1. 五账号登录 + 身份/权限集 ==")
    for role in ACCOUNTS:
        tokens[role] = login(role)
    check(len(tokens) == 5, "五账号全部登录成功")
    for role in ("purchase", "warehouse", "production", "qa"):
        me = api(role, "GET", "/auth/me")
        check(me.get("role") == role, f"/auth/me 返回 role={me.get('role')}（期望 {role}）")
    me_admin = api("admin", "GET", "/auth/me")
    check("po.create" in me_admin.get("actions", [])
          and "inspect.judge" in me_admin.get("actions", []), "admin 持全量动作集")
    me_wh = api("warehouse", "GET", "/auth/me")
    check("po.create" not in me_wh.get("actions", [])
          and "po.receive" in me_wh.get("actions", []), "warehouse 权限集无 po.create 有 po.receive")

    print("== 2. 种子主数据确认（物料批次开关/UDI、供应商资质三态）==")
    mats = api("admin", "GET", "/materials", params={"page_size": 100})["items"]
    mat = {m["code"]: m for m in mats}
    for code in ("RAW-SCREW-M3X8", "SEMI-BRACKET", "FG-BRACKET"):
        check(code in mat, f"物料 {code} 存在")
        check(mat[code]["batch_required"] is True, f"物料 {code} batch_required=True")
    check(mat["FG-BRACKET"].get("udi_di"), "FG-BRACKET 带 udi_di（UDI-DI）")
    raw_id = mat["RAW-SCREW-M3X8"]["id"]
    semi_id = mat["SEMI-BRACKET"]["id"]
    fg_id = mat["FG-BRACKET"]["id"]
    udi_di = mat["FG-BRACKET"]["udi_di"]
    partners = api("admin", "GET", "/partners", params={"page_size": 100})["items"]
    partner = {p["code"]: p for p in partners}
    sup1 = partner["SUP-001"]["id"]
    sup3 = partner["SUP-003"]["id"]
    cus1 = partner["CUS-001"]["id"]
    certs = api("admin", "GET", "/partner-certs", params={"page_size": 100})["items"]
    cert_by_no = {c["cert_no"]: c for c in certs}
    for no in ("CERT-DEMO-001", "CERT-DEMO-002", "CERT-DEMO-003"):
        check(no in cert_by_no and cert_by_no[no]["audit_status"] == "audited",
              f"资质 {no} 存在且 audited")
    check(cert_by_no["CERT-DEMO-003"]["valid_until"] < str(date.today()),
          "CERT-DEMO-003（SUP-003）已过期")
    check(cert_by_no["CERT-DEMO-002"]["valid_until"] >= str(date.today()),
          "CERT-DEMO-002（SUP-002）临期未过期")
    expiring = api("admin", "GET", "/partner-certs/expiring", params={"days": 30})
    check(any(c["cert_no"] == "CERT-DEMO-002" for c in expiring["items"]),
          "SUP-002 进入临期预警列表")
    whs = api("admin", "GET", "/warehouses", params={"page_size": 100})["items"]
    main_wh = next(w for w in whs if w["code"] == "MAIN")
    wh_main = main_wh["id"]

    print("== 3. purchase 建 PO（SUP-001）→ 审核；过期供应商拦截 ==")
    po = api("purchase", "POST", "/purchase-orders", json={
        "partner_id": sup1, "order_date": str(date.today()),
        "lines": [{"material_id": raw_id, "qty": 40, "unit_price": 5,
                   "warehouse_id": wh_main}]})
    check(po["status"] == "draft", f"PO {po['doc_no']} 草稿（RAW 40@5）")
    line_id = po["lines"][0]["id"]
    po = api("purchase", "POST", f"/purchase-orders/{po['id']}/approve")
    check(po["status"] == "approved", "PO 审核通过（purchase 自审，权限允许）")
    po_id = po["id"]
    api_expect("purchase", "POST", "/purchase-orders", 400, {
        "partner_id": sup3, "order_date": str(date.today()),
        "lines": [{"material_id": raw_id, "qty": 1, "unit_price": 1,
                   "warehouse_id": wh_main}]})

    print("== 4. 越权矩阵抽测（全部 403）==")
    api_expect("warehouse", "POST", "/purchase-orders", 403, {
        "partner_id": sup1, "order_date": str(date.today()),
        "lines": [{"material_id": raw_id, "qty": 1, "unit_price": 1,
                   "warehouse_id": wh_main}]})
    api_expect("production", "POST", "/purchase-orders", 403, {
        "partner_id": sup1, "order_date": str(date.today()),
        "lines": [{"material_id": raw_id, "qty": 1, "unit_price": 1,
                   "warehouse_id": wh_main}]})

    print("== 5. warehouse 分批入库：LOT-acc-1 / LOT-acc-2 各 20 ==")
    po = api("warehouse", "POST", f"/purchase-orders/{po_id}/receive", [
        {"line_id": line_id, "qty": 20, "warehouse_id": wh_main,
         "batch_no": "LOT-acc-1"}])
    check(po["status"] == "partial", "第一次入库 20（LOT-acc-1）→ partial")
    po = api("warehouse", "POST", f"/purchase-orders/{po_id}/receive", [
        {"line_id": line_id, "qty": 20, "warehouse_id": wh_main,
         "batch_no": "LOT-acc-2"}])
    check(po["status"] == "completed", "第二次入库 20（LOT-acc-2）→ completed")
    api_expect("purchase", "POST", f"/purchase-orders/{po_id}/receive", 403, [
        {"line_id": line_id, "qty": 1, "warehouse_id": wh_main,
         "batch_no": "LOT-acc-x"}])
    batches = api("qa", "GET", "/batches",
                  params={"material_id": raw_id, "page_size": 50})["items"]
    b_map = {b["batch_no"]: b for b in batches}
    check("LOT-acc-1" in b_map and "LOT-acc-2" in b_map, "两批次台账记录生成")
    check(b_map["LOT-acc-1"]["insp_status"] == "pending"
          and b_map["LOT-acc-2"]["insp_status"] == "pending", "入库批次检验状态 pending")
    b1, b2 = b_map["LOT-acc-1"]["id"], b_map["LOT-acc-2"]["id"]

    print("== 6. qa 待检判定：LOT-acc-1 pass → qualified ==")
    pending = api("qa", "GET", "/batches",
                  params={"material_id": raw_id, "insp_status": "pending",
                          "page_size": 50})["items"]
    check({b["batch_no"] for b in pending} == {"LOT-acc-1", "LOT-acc-2"},
          "qa 待检批次列表含 LOT-acc-1/2")
    rec = api("qa", "POST", f"/batches/{b1}/inspect", json={
        "items": [{"name": "外观", "result": "合格"},
                  {"name": "尺寸", "result": "合格"}],
        "conclusion": "pass", "remark": "v2 冒烟检验"})
    check(rec["conclusion"] == "pass" and rec["inspector"] == "qa", "检验记录录入（pass，检验人 qa）")
    b1_detail = api("qa", "GET", f"/batches/{b1}")
    check(b1_detail["insp_status"] == "qualified", "LOT-acc-1 → qualified（放行）")
    api_expect("production", "POST", f"/batches/{b1}/inspect", 403, {
        "items": [{"name": "外观", "result": "合格"}], "conclusion": "pass"})

    print("== 7. production MRP → 转单 → MO1(SEMI) 下达/领料/完工 ==")
    run = api("production", "POST", "/mrp/run", json={
        "demand": [{"material_id": fg_id, "qty": 10}]})
    sugs = {(s["material_id"], s["type"]): dec(s["net_qty"]) for s in run["suggestions"]}
    expect("MRP FG make 净需求", sugs.get((fg_id, "make"), 0), 10)
    expect("MRP SEMI make 净需求", sugs.get((semi_id, "make"), 0), 10)
    check((raw_id, "buy") not in sugs, "RAW 无 buy 建议（qualified 20 恰好抵扣毛需求 20）")
    semi_sug = next(s for s in run["suggestions"]
                    if s["material_id"] == semi_id and s["type"] == "make")
    fg_sug = next(s for s in run["suggestions"]
                  if s["material_id"] == fg_id and s["type"] == "make")
    conv = api("production", "POST", "/mrp/suggestions/convert",
               json={"ids": [semi_sug["id"]]})
    mo1 = conv["converted"][0]["doc_id"]
    mo = api("production", "GET", f"/manufacturing-orders/{mo1}")
    check(mo["status"] == "draft" and dec(mo["planned_qty"]) == 10,
          f"SEMI 建议转单 → MO1 {mo['doc_no']} 草稿计划 10")
    mo = api("production", "POST", f"/manufacturing-orders/{mo1}/approve")
    check(mo["status"] == "approved", "MO1 下达")
    mo = api("production", "POST", f"/manufacturing-orders/{mo1}/issue", json={
        "lines": [{"material_id": raw_id, "qty": 20, "batch_id": b1}]})
    detail = api("production", "GET", f"/manufacturing-orders/{mo1}")
    expect("MO1 领料成本（20 RAW @5，合格批次）", detail["issued_qty_cost"], 100)
    api("production", "POST", f"/manufacturing-orders/{mo1}/receive",
        json={"qty": 10, "extra_cost": 0})
    check(api("production", "GET", f"/manufacturing-orders/{mo1}")["status"] == "completed",
          "MO1 完工入库 10 → completed")
    semi_batches = api("qa", "GET", "/batches",
                       params={"material_id": semi_id, "page_size": 50})["items"]
    check(len(semi_batches) == 1 and semi_batches[0]["insp_status"] == "qualified",
          f"SEMI 成品批次自动生成且 qualified（{semi_batches[0]['batch_no']}）")
    semi_batch = semi_batches[0]["id"]

    print("== 8. MO2(FG) 下达/领料/完工 → 成品批次自动 ==")
    conv = api("production", "POST", "/mrp/suggestions/convert",
               json={"ids": [fg_sug["id"]]})
    mo2 = conv["converted"][0]["doc_id"]
    mo = api("production", "POST", f"/manufacturing-orders/{mo2}/approve")
    check(mo["status"] == "approved", "MO2 下达")
    api("production", "POST", f"/manufacturing-orders/{mo2}/issue", json={
        "lines": [{"material_id": semi_id, "qty": 10, "batch_id": semi_batch}]})
    api("production", "POST", f"/manufacturing-orders/{mo2}/receive",
        json={"qty": 10, "extra_cost": 0})
    fg_batches = api("qa", "GET", "/batches",
                     params={"material_id": fg_id, "page_size": 50})["items"]
    check(len(fg_batches) == 1 and fg_batches[0]["insp_status"] == "qualified",
          f"FG 成品批次自动生成且 qualified（{fg_batches[0]['batch_no']}）")
    fg_batch = fg_batches[0]["id"]

    print("== 9. 合规拦截：pending 批次领料 400（MO3 试领后关闭）==")
    mo3 = api("production", "POST", "/manufacturing-orders", json={
        "material_id": semi_id, "planned_qty": 2, "warehouse_id": wh_main,
        "remark": "v2 冒烟-pending 拦截试单"})["id"]
    api("production", "POST", f"/manufacturing-orders/{mo3}/approve")
    api_expect("production", "POST", f"/manufacturing-orders/{mo3}/issue", 400, {
        "lines": [{"material_id": raw_id, "qty": 4, "batch_id": b2}]})
    api("production", "POST", f"/manufacturing-orders/{mo3}/close")
    b2_detail = api("qa", "GET", f"/batches/{b2}")
    check(b2_detail["insp_status"] == "pending" and dec(b2_detail["stock_qty"]) == 20,
          "LOT-acc-2 保持 pending 且存量未被领用")

    print("== 10. purchase 建 SO → approve → warehouse 按批次出库（UDI 校验）==")
    so = api("purchase", "POST", "/sales-orders", json={
        "partner_id": cus1, "order_date": str(date.today()),
        "lines": [{"material_id": fg_id, "qty": 10, "unit_price": 25,
                   "warehouse_id": wh_main}]})
    so = api("purchase", "POST", f"/sales-orders/{so['id']}/approve")
    check(so["status"] == "approved", f"SO {so['doc_no']} 审核通过")
    so_line = so["lines"][0]["id"]
    so = api("warehouse", "POST", f"/sales-orders/{so['id']}/ship", [
        {"line_id": so_line, "qty": 10, "warehouse_id": wh_main,
         "batch_id": fg_batch}])
    check(so["status"] == "completed", "SO 出库 10（合格成品批次）→ completed")
    fg_batch_detail = api("warehouse", "GET", f"/batches/{fg_batch}")
    expect("FG 批次出库后现存", fg_batch_detail["stock_qty"], 0)
    mat_fg = api("warehouse", "GET", f"/materials/{fg_id}")
    check(mat_fg["udi_di"] == udi_di,
          f"出库 UDI 信息可查：UDI-DI={mat_fg['udi_di']}，PI=批号 {fg_batch_detail['batch_no']}")
    ars = api("admin", "GET", "/receivables", params={"partner_id": cus1})["items"]
    expect("应收发生额", sum(dec(a["amount"]) for a in ars), 250)
    ar = ars[0]

    print("== 11. admin 核销 + 对账 ==")
    aps = api("admin", "GET", "/payables", params={"partner_id": sup1})["items"]
    expect("应付发生额合计（40@5，按收货批次两张）", sum(dec(p["amount"]) for p in aps), 200)
    ap_open = aps[0]
    st = api("admin", "POST", "/settlements", json={
        "type": "payment", "partner_id": sup1, "date": str(date.today()),
        "remark": "v2 冒烟-部分付款",
        "lines": [{"target_type": "ap", "target_id": ap_open["id"], "amount": 80}]})
    expect("付款核销单金额", st["amount"], 80)
    st2 = api("admin", "POST", "/settlements", json={
        "type": "receipt", "partner_id": cus1, "date": str(date.today()),
        "remark": "v2 冒烟-全额收款",
        "lines": [{"target_type": "ar", "target_id": ar["id"], "amount": 250}]})
    expect("收款核销单金额", st2["amount"], 250)
    s_sup = api("admin", "GET", "/finance/statement",
                params={"partner_id": sup1, "type": "supplier"})
    expect("供应商对账-发生", s_sup["totals"]["total_amount"], 200)
    expect("供应商对账-已核销", s_sup["totals"]["total_settled"], 80)
    expect("供应商对账-余额", s_sup["totals"]["total_balance"], 120)
    s_cus = api("admin", "GET", "/finance/statement",
                params={"partner_id": cus1, "type": "customer"})
    expect("客户对账-发生", s_cus["totals"]["total_amount"], 250)
    expect("客户对账-余额", s_cus["totals"]["total_balance"], 0)

    print("== 12. 追溯：FG 批次正查 / RAW 批次反查 ==")
    tf = api("qa", "GET", f"/batches/{fg_batch}/trace-forward")
    tf_nos = [l["to_batch"]["batch_no"] for l in tf["links"]]
    check(semi_batches[0]["batch_no"] in tf_nos, f"FG 正查第 1 层：SEMI 批次 {tf_nos}")
    check("LOT-acc-1" in tf_nos, "FG 正查递归到第 2 层：RAW 批次 LOT-acc-1")
    raw_links = [l for l in tf["links"] if l["to_batch"]["batch_no"] == "LOT-acc-1"]
    check(bool(raw_links) and raw_links[0]["partner"] == partner["SUP-001"]["name"],
          f"RAW 批次带来源供应商：{raw_links[0]['partner'] if raw_links else '无'}")
    tb = api("qa", "GET", f"/batches/{b1}/trace-backward")
    tb_nos = [l["to_batch"]["batch_no"] for l in tb["links"]]
    check(semi_batches[0]["batch_no"] in tb_nos and fg_batches[0]["batch_no"] in tb_nos,
          f"RAW 反查到 SEMI 与 FG 成品批次两层：{tb_nos}")

    print("== 13. 资质录入/审核 + 审计留痕 ==")
    cert = api("purchase", "POST", "/partner-certs", json={
        "partner_id": sup1, "cert_name": "质量管理体系认证",
        "cert_no": f"CERT-S6-{suffix}", "issuer": "认证机构",
        "valid_from": str(date.today() - timedelta(days=365)),
        "valid_until": str(date.today() + timedelta(days=365)),
        "file_ref": f"/data/certs/CERT-S6-{suffix}.pdf"})
    check(cert["audit_status"] == "draft", "purchase 录入资质 → draft")
    cert = api("qa", "POST", f"/partner-certs/{cert['id']}/audit")
    check(cert["audit_status"] == "audited" and cert["audited_by"] == "qa",
          "qa 审核资质 → audited（audited_by=qa）")
    api_expect("warehouse", "POST", "/partner-certs", 403, {
        "partner_id": sup1, "cert_name": "越权试单", "cert_no": f"CERT-S6-X-{suffix}",
        "valid_from": str(date.today()), "valid_until": str(date.today())})
    recs = api("admin", "GET", "/audit/records",
               params={"doc_type": "batch", "page_size": 100})["items"]
    check(any(r["action"] == "inspection" and r["actor"] == "qa" for r in recs),
          f"audit/records 含检验判定留痕（inspection，共 {len(recs)} 条 batch 记录）")
    crecs = api("admin", "GET", "/audit/records",
                params={"doc_type": "partner_cert", "page_size": 100})["items"]
    actions = {r["action"] for r in crecs}
    check("create" in actions and "audit" in actions,
          f"audit/records 含资质录入/审核留痕（{sorted(actions)}）")
    logs = api("admin", "GET", "/audit/logs", params={"page_size": 5})
    check(logs["total"] > 0, f"audit/logs 留痕 {logs['total']} 条")

    return finish()


def finish() -> int:
    print(f"\n==== v2 冒烟结果：{len(passed)} 项通过，{len(failures)} 项失败 ====")
    if failures:
        print("失败点：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL V2 SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
