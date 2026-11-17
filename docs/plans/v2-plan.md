# erp-lite v2 实现计划（GMP + RBAC）

> **For Hermes:** Use subagent-driven-development, stage by stage.

## 阶段

| 阶段 | 内容 | 依赖 |
|---|---|---|
| V2-S1 | RBAC：users.role、JWT role claim、rbac.py 权限表、require_action deps、/auth/me、用户管理接口、种子 5 账号 + tests（各角色越权矩阵） | 无 |
| V2-S2 | 批次内核：Batch 表+迁移、materials.batch_required/UDI 字段、inventory_moves.batch_id、post_move/库存口径按批次、批次 CRUD+台账 API + tests | V2-S1 |
| V2-S3 | 检验放行+追溯：InspectionRecord、inspect/dispose API、领料/出库/完工接入批次与 qualified 校验、PO receive 生成待检批次、正反向追溯 API + tests | V2-S2 |
| V2-S4 | 供应商资质：PartnerCert、audit API、PO 资质拦截、临期预警 + tests | V2-S1（与 S2/S3 文件区可并行，但 main.py 挂路由需串行合并） |
| V2-S5 | 前端：身份切换/权限显隐/新四页面/批次选择控件/UDI 字段 | V2-S3 契约（可与 S4 并行） |
| V2-S6 | 验收：种子含演示数据（临期/过期供应商、2层BOM批次链）、smoke 扩展（五角色切换越权、追溯、放行拦截）、DoD 总检 | 全部 |

## 约定

- 沿用 v1 流程：TDD 红先绿后、每阶段 commit、实现后审查
- 兼容铁律：v1 非批次物料行为不变，现有 140 测试断言不改、必须全绿
- 契约改动先写 DESIGN-v2.md 再实现
