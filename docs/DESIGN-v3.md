# erp-lite v3 技术设计：全链路闭环重构

> 基线 docs/DESIGN.md + DESIGN-v2.md，本文件为 v3 增量定稿。背景：2026-11-18 全链路审计确认 v2 存在
> 四条断头路（处置/复判/销售退货/盘点）、四个必失败功能、角色缺位、使用层零串联。v3 目标：每个业务
> 动作满足**闭环五要素**（状态流转可达终点 / 数量金额自动联动 / 审计留痕 / 前端入口 / 下一步指引）。

## 1. 决策记录（2026-11-18 用户确认）

| 决策点 | 结论 |
|---|---|
| 路线 | v3 系统性重构：保留技术栈与骨架（状态机/审计/核销/追溯），补闭环 |
| 角色 | 八角色：admin / purchase / sales / warehouse / production / qc / qa / finance |
| 质检口径 | 先收后检：收货生成 pending 批次 → qc 录入 → qa 复核放行；处置产生真实单据与账务联动 |
| 增强项 | 审批拒绝退回、领料退回、核销冲回、资质撤销 全部纳入 |

## 2. 两条贯穿规则

- **规则 A｜不得自审**：审核/放行/复核/撤销类动作，操作人 ≠ 单据创建/录入人（400）。适用：PO/SO
  审-拒、MO 下达-拒、资质审-撤、检验复核。admin 无豁免（演示职责分离）。
- **规则 B｜质检双段制**：所有批次（采购原料 + MO 完工成品）统一 `pending → qc 录入检验记录
  （建议结论）→ qa 复核判定（qualified/rejected）→ rejected 走处置`。成品不再默认 qualified。

## 3. 角色权限矩阵（services/rbac.py 重写定稿）

| 动作键 | admin | purchase | sales | warehouse | production | qc | qa | finance |
|---|---|---|---|---|---|---|---|---|
| master.write | ✓ | | | | | | | |
| cert.write（资质录入） | ✓ | ✓ | | | | | | |
| cert.audit / cert.revoke | ✓ | | | | | | ✓ | |
| po.create/update/void/close | ✓ | ✓ | | | | | | |
| po.approve / po.reject | ✓ | ✓ | | | | | | |
| po.receive | ✓ | | | ✓ | | | | |
| so.create/update/void/close | ✓ | | ✓ | | | | | |
| so.approve / so.reject | ✓ | | ✓ | | | | | |
| so.ship | ✓ | | | ✓ | | | | |
| sales-return.create | ✓ | | ✓ | | | | | |
| bom.write / mrp.run / mo.*（建/下达-拒/领料/退料/完工/关/作废） | ✓ | | | | ✓ | | | |
| batch.inspect（qc 录入） | ✓ | | | | | ✓ | | |
| batch.judge（qa 判定）/ batch.dispose | ✓ | | | | | | ✓ | |
| batch.trace | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| stockcount.create | ✓ | | | ✓ | | | | |
| stockcount.post | ✓ | | | | | | | ✓ |
| settlement.create / settlement.reverse / statement.read | ✓ | | | | | | | ✓ |
| ap.read | ✓ | ✓ | | | | | | ✓ |
| ar.read | ✓ | | ✓ | | | | | ✓ |
| audit.read | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| users.manage | ✓ | | | | | | | |

读权限注：批次台账/库存/追溯全员可读；AP 列表 purchase/finance/admin，AR 列表 sales/finance/admin。

## 4. 数据模型增量（Alembic 0005，SQLite dev 库直接重建亦可）

```python
users.role           # 枚举扩展八角色；存量 qa → qa（复核），新增 qc
AuditRecord          # + role(str), doc_no(str 可空)
OperationLog         # + doc_type(str 可空), doc_id(int 可空)
purchase_orders / sales_orders      # + reject_reason(str 可空)   # reject 退回 draft 留因
manufacturing_orders # + received_qty, completed_qty(Decimal 持久化列，修「完工量恒0」)
                     # + reject_reason(str 可空)
batches              # + loss_amount(Decimal, 报废/费用化损失口径)
partner_certs        # audit_status + revoked；+ revoke_reason, revoked_by, revoked_at
inspection_records   # 语义重构：recorder(qc 录入人), suggest_conclusion(qc 建议),
                     # judge_by(qa 判定人), judged_at, conclusion(final) —— 存量重 seed
settlements          # + reversed_settlement_id(可空，指向被冲回单)

PurchaseReturn       # id, doc_no, po_id, batch_id, qty, amount, reason,
                     # created_by, created_at   # 处置(return) 即建此单
SalesReturn          # id, doc_no, so_id, batch_id, qty, amount, reason,
                     # created_by, created_at
StockCount           # id, doc_no, warehouse_id, status(draft/posted),
                     # created_by, posted_by, posted_at
StockCountLine       # count_id, material_id, batch_id(可空), system_qty,
                     # counted_qty, diff_qty
```

## 5. 链路闭环语义（后端动作清单）

### 5.1 采购
- `PO reject`：approved→draft，reject_reason 留痕（AuditRecord + op_log）。
- `receive`：收货仓库强制 = PO 行仓库（400）；批次号跨单不复用（同号存在且非本次 → 400
  提示换号）；批次创建写 AuditRecord。
- `dispose(return)`：创建 PurchaseReturn（qa 发起）→ 同事务：红字入库流水（带批次，qty 负）→
  批次现存冲减 → AP 红字（负额 Payable 引用原 AP，未清额对冲）→ PO received_qty 冲减
  （completed→partial 允许重收）→ AuditRecord。
- `dispose(destroy)`：报废流水（带批次）清现存 → loss_amount = 批次现存金额 → AP 保留 → closed。
- MRP convert 创建 PO 前调 `ensure_supplier_certified`（堵旁路）；转单价 0 写 op_log 警告。

### 5.2 销售
- `SO reject` 同 PO；出库 API/SO 详情返回行批次号与 UDI（udi_di + PI=batch_no）。
- `sales-return.create`（sales 发起）→ 红字出库流水 → 批次回 pending（数量回现存，待重检）→
  AR 红字冲减 → so.shipped_qty 冲减（completed→partial）→ 审计。

### 5.3 生产
- MO `void`（draft）、`reject`（approved→draft + 原因）。
- `unissue`：按领料行退回 qty（红字领料流水带批次 → 批次可用量恢复 → issued_qty_cost 冲减）。
- `receive`：完工批次 pending（规则 B）；received_qty/completed_qty 持久化累加；零领料拦截
  （400「需先领料」）。
- `close`：completed=0 时剩余领料成本写**带批次**费用化 adjust 流水（loss 记批次/物料），
  消除成本悬空与批次/物料均价分叉。

### 5.4 库存
- `StockCount`：warehouse 建 draft（system_qty 快照）→ 录 counted_qty → finance/admin post
  → 差异写带批次 adjust 流水 → posted。批次物料必须按批次盘。
- `/inventory/stock`：批次物料返回 qualified/pending/rejected 三口径；仓管可见即解释可发性。

### 5.5 财务
- `settlement.reverse`：finance 发起 → 生成冲回单（reversed_settlement_id 指原单）→
  原 bill settled_amount 回减、状态回退（settled→partial→open）→ 审计。
- `statement` 重写口径：期初未清 = 起日前 Σ(amount−settled)；期间新增、期间核销（按核销单
  日期，含对期初单据的核销）→ 期末 = 期初 + 期间新增 − 期间核销，明细可勾稽。

### 5.6 资质
- `cert revoke`（qa）：audited→revoked + 原因/人/时；有效性判断排除 revoked（拦截/临期口径
  不含）；留痕 approve_audit 同款。

### 5.7 审计与工作台
- AuditRecord 全覆盖新增：users 增改、AP/AR 创建、批次创建、状态不变 receive/ship、盘点
  post、退货/冲回/撤销/退料。action 字符串收敛枚举（create/update/status/void/close/reject/
  issue/unissue/receive/ship/inspection/judge/dispose/audit/revoke/settle/reverse/post）。
- `/audit/records` + actor/role/from/to 过滤；`/audit/logs` + doc_type/doc_id/actor。
- `GET /dashboard`：按当前角色返回待办 `[{key,label,count,target}]`：
  purchase 待审 PO；sales 待审 SO；warehouse 待收 PO/可出库 SO/待盘；production 待下达/
  待领料/待完工 MO；qc 待检批次；qa 待判定批次/待审资质；finance 待核销 AP/AR/待 post 盘点。
- PO/SO 详情审计返回上限 20 → 200。

## 6. 前端增量

- **Dashboard.vue 为登录首页**：按 /auth/me 权限渲染待办卡片（计数+点击直达）。
- **DocLink 组件**：source_type+source_id/doc_no → 可点击跳转；替换 AP/AR/批次/流水/MRP/
  核销/审计里的纯文本引用。doc_type→路由映射表统一维护。
- 新页面：采购退货（PurchaseReturns.vue）、销售退货（SalesReturns.vue）、盘点（StockCounts.vue，
  建/录/post 三步）。
- 改造：
  - Inspections.vue 双 tab：qc「待录入」（录检项+建议）→ qa「待判定」（复核 pass/fail）→
    「不合格处置」tab 保留（处置=建退货单/报废，成功提示含单号）。
  - OrderPage.vue：reject 按钮（approved 态，需填原因）；partial 不再显示作废；出库/领料批次
    下拉空态解释 + 展示批次现存；创建行不静默丢弃。
  - ManufacturingOrderList.vue：显示 received/completed_qty；remark/extra_cost 录入；draft
    作废；审计 tab 补齐；unissue 入口（领料明细行）。
  - Mrp.vue：buy 建议转单弹供应商选择（必填校验）。
  - Statement.vue：契约对齐（type=supplier/customer，渲染 details/totals/期初），勾稽展示。
  - FinanceBill.vue：partner 筛选 + 来源 DocLink；Settlements.vue：partner 筛选 + remark +
    冲回按钮（finance）。
  - Inventory.vue：三口径列（合格/待检/不合格）。
  - Audit.vue：actor/时间/doc_type(batch/partner_cert) 过滤 + 动作中文映射补全 + doc_no 展示。
  - Batches.vue：处置方式/备注列 + loss_amount + 溯源列头修正。
  - PartnerCerts.vue：qa 撤销按钮（原因弹窗）+ revoked tag。
  - 权限缓存：路由守卫每次切换拉 /auth/me（本地接口开销可忽略）；Users.vue 提示语纠正。
  - 422 detail 数组转中文逐条 message。
- 操作成功提示统一带下一步指引（如「批次 X 已进待检，qc 可录入检验记录」）。

## 7. 测试与验收

- 既有 140 测试适配八角色契约（qa→qc/qa 拆分、inspect 两段）。
- 新增测试：不得自审（po/so/cert/inspect）、reject 退回、dispose(return) 冲 AP+PO 回 partial、
  dispose(destroy) 清库存+loss、销售退货回 pending+冲 AR、盘点 post 勾稽、核销冲回恢复状态、
  MRP 转单资质拦截、unissue 恢复批次可用量、close 费用化带批次、dashboard 角色视图、三口径。
- smoke_v3.py：八账号接力（purchase 下单→sales 下单并行→warehouse 收发→qc/qa 检验放行→
  production 领退产→finance 核销冲回对账→处置退货报废→盘点）+ 越权/自审拦截断言。
- DoD（用户页面检查清单随交付输出）。

## 8. Non-goals（v3 仍不做）

电子签名、审批工作流引擎、对象存储、多租户/组织架构、库位级管理（批次即隔离口径）、
CAPA/不良事件、科目体系（损失记批次/单据字段，不引记账模块）。
