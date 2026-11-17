# erp-lite v2 技术设计补充（GMP + RBAC）

> 基线见 docs/DESIGN.md，本文件只写增量。前后端契约冲突时以本文为准并同步回 DESIGN.md。

## 1. 数据模型增量（Alembic 0003/0004）

```python
# users: + role (Enum: admin/purchase/warehouse/production/qa, 默认 admin)
# materials: + batch_required (bool, 默认 False)
#            + udi_di (str, 可空, 成品用) + udi_pi_rule (str, 可空)
# inventory_moves: + batch_id (FK batches.id, 可空)   # 非批次物料为空，v1 数据合法
# purchase_order_lines: + 无变化（入库时按行生成批次，一次入库一批）

class Batch(Base):            # 批次台账（新表）
    id, batch_no(unique), material_id, source_type(purchase_in/mo_receipt),
    source_id, supplier_id(原料批次记录来源供应商，成品为空),
    insp_status(pending/qualified/rejected/closed),
    disposition(可空: return/destroy), disposition_remark,
    created_by, created_at

class InspectionRecord(Base): # 检验记录（新表）
    id, batch_id, items_json(检项列表), conclusion(pass/fail),
    inspector, inspected_at, remark

class PartnerCert(Base):      # 供应商资质（新表）
    id, partner_id, cert_name, cert_no, issuer,
    valid_from, valid_until, file_ref(路径引用字符串),
    audit_status(draft/audited), audited_by, audited_at
```

## 2. 权限模型

- **实现形态**：硬编码权限表 `services/rbac.py`，`ROLE_ACTIONS: dict[str, set[str]]`，动作键如 `po.create/po.receive/so.ship/mo.issue/inspect.judge/batch.trace/settlement.create/master.write/users.manage`；deps 里 `require_action("po.create")` 校验 JWT payload 里的 role（JWT 增发 role claim）。403 返回中文「当前角色无权执行该操作」。
- 不做可配置权限（YAGNI，演示系统角色固定）；admin 全量通过。
- JWT 里带 role 意味着改角色需重登录——可接受，README 注明。

## 3. 库存/成本口径变化

- 批次物料：现存量/均价/可用量全部按 (material_id, batch_id) 维度；`get_stock` 增加 group-by-batch；`post_move` 带批次参数并校验批次存在
- `可用量 = Σ qualified 批次现存`；出库/领料动作先校验目标批次 `insp_status==qualified`
- 非批次物料：完全走 v1 路径（batch_id=None），物料级均价
- MRP 在途/现存量计算对批次物料按可用量口径（只计 qualified）

## 4. API 增量

| 方法+路径 | 说明 | 角色 |
|---|---|---|
| GET /auth/me | 当前身份+权限集 | 登录用户 |
| GET /users · POST /users · PUT /users/{id}/role | 用户与角色管理 | admin |
| GET/POST /batches?material_id=&insp_status= | 批次台账 | 全员可查 |
| GET /batches/{id}/trace-forward | 正向追溯：本批次用到了哪些原料批次+供应商 | 全员 |
| GET /batches/{id}/trace-backward | 反向召回：本批次流向哪些成品批次+单据 | 全员 |
| POST /batches/{id}/inspect | 录入检验记录+判定（合格/不合格） | qa/admin |
| POST /batches/{id}/dispose | 不合格批次处置（退货/销毁→closed） | qa/admin |
| GET/POST /partner-certs · POST /{id}/audit | 资质维护与审核 | admin/purchase 录入，qa/admin 审核 |
| GET /partner-certs/expiring?days=30 | 临期资质预警 | 全员 |

修改既有接口语义：
- `POST /purchase-orders/{id}/receive`：body 增加 `batch_no`（批次物料必填），入库后批次 insp_status=pending；**注意流程顺序**：入库生成待检批次→QA 合格后批次才可被领料/出库。GMP 语义的「放行入库使用」落地为：物理入库允许（进待检区），领用/出库被禁——文档明确此口径
- `POST /manufacturing-orders/{id}/issue` / `POST /sales-orders/{id}/ship`：body 增加 `batch_id`（批次物料必填），校验 qualified
- MO receive：成品批次自动生成（batch_no=MO 单号后缀），status=qualified，来源 supplier 为空
- PO create/approve：供应商资质有效校验（存在 audited 且 valid_until ≥ today 的证照）

## 5. 前端增量

- 顶栏身份徽章 + 「切换演示身份」下拉（5 账号快速登录切换）
- 登录页演示账号快捷按钮（5 个角色一键填入）
- 菜单/按钮按 `/auth/me` 权限集显隐；越权操作后端 403 前端 message 提示
- 新页面：批次台账（含状态 tag：待检黄/合格绿/不合格红/已关闭灰）、批次追溯查询（输入批次号→正反向两层表格）、检验工作台（qa：待检批次列表+判定弹窗）、供应商资质管理（列表+临期预警页签）
- PO 入库弹窗增加批次号输入；MO 领料/SO 出库弹窗增加合格批次下拉选择
- 物料表单增加 batch_required 开关与 UDI 字段（成品时显示）

## 6. 测试要点

- RBAC：每角色越权用例（至少覆盖 po.create×warehouse=403、inspect.judge×production=403）
- 批次：pending/rejected 批次领料/出库 400；qualified 正常；追溯正反向正确性（构造 2 层 BOM 场景）
- 资质：无证/过期供应商 PO 400；临期列表正确
- 兼容：v1 非批次物料全量旧行为回归（现有 140 测试不改断言全绿）
