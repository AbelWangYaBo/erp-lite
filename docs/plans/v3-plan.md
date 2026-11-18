# erp-lite v3 实施计划

> 设计见 docs/DESIGN-v3.md。阶段顺序即依赖顺序，每阶段结束工作区可提交、pytest 可跑。

## Phase B1 数据层（任务 #2）
- Alembic 0005：八角色枚举、审计表新列、PO/SO/MO reject_reason、MO received/completed_qty、
  Batch.loss_amount、PartnerCert 撤销字段、InspectionRecord 双段字段、Settlement.reversed_id、
  新表 purchase_returns / sales_returns / stock_counts(+lines)
- 验收：`alembic upgrade head` 幂等通过；models 与迁移一致

## Phase B2 权限与规则（#3）
- rbac.py 八角色矩阵（DESIGN §3）；deps 增 `ensure_not_self(actor, doc_creator)` 辅助
- 验收：单测覆写矩阵关键格

## Phase B3 批次与检验（#4）
- 检验双段（qc inspect / qa judge，judge≠recorder）、dispose 闭环（return→PurchaseReturn
  全联动；destroy→报废流水+loss_amount）、批次不复用、收货仓库校验
- 验收：新增测试过；旧 inspect 用例迁移

## Phase B4 单据与财务（#5 #6 #7 #8 #9）
- PO/SO reject；采购/销售退货端点与联动；盘点单；三口径库存；MO void/reject/unissue/
  完工量持久化/成品 pending/关闭费用化带批次/零领料拦截；核销冲回；对账单勾稽重写；
  cert revoke；MRP 转单资质拦截
- 验收：对应测试全过

## Phase B5 审计与工作台（#10）
- 审计全覆盖（清单见 DESIGN §5.7）+ 过滤参数；GET /dashboard；详情审计上限 200
- 验收：audit 过滤与 dashboard 角色视图测试

## Phase B6 测试与种子冒烟（#11 #12）
- 既有测试适配 + 新增闭环测试全绿；seed_v3（八账号+演示数据）、smoke_v3 全链路断言
- 验收：`uv run pytest` 全绿；`uv run python ../scripts/smoke_v3.py` 全链路通过

## Phase F1 前端（#13 #14）
- Dashboard、DocLink、三新页、改造清单见 DESIGN §6
- 验收：`npm run build` 通过；dev server 页面手测八角色

## Phase E2 端到端（#15）
- 重建 dev 库（迁移+seed_v3）→ 起后端/前端 → smoke_v3 → 输出页面检查指引 → 提交
