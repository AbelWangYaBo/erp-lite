# erp-lite 技术设计（v1）

## 1. 架构

```
[Vue 3 + Vite + Element Plus + Pinia]  --build-->  [nginx 静态托管 + 反代 /api]
                                                          |
                                     [FastAPI + SQLAlchemy 2.0 + Alembic]
                                                          |
                                     [PostgreSQL 16]  (volume 持久化)
```

- 后端：Python 3.13，uv 管理（pyproject + uv.lock），包名 `erp_lite`，入口 `uvicorn erp_lite.main:app`
- 前端：`web/` 目录，npm 构建，产物由 nginx 容器托管并反代 `/api` 到 api 容器
- 部署：`docker-compose.yml` 三服务：`db`(postgres:16-alpine) / `api` / `web`(nginx)。DB 数据挂 volume；api 启动时自动 `alembic upgrade head`
- 认证：单管理员（用户名/密码存环境变量 `ERP_ADMIN_USER/ERP_ADMIN_PASSWORD`，默认 admin/admin123 仅限本地），登录签发 JWT，全部 /api 需鉴权（/api/v1/auth/login 除外）

## 2. 目录结构

```
erp-lite/
├── backend/
│   ├── pyproject.toml / uv.lock
│   ├── alembic/                 # 迁移
│   ├── erp_lite/
│   │   ├── main.py              # FastAPI app, CORS, 静态健康检查
│   │   ├── config.py            # env 配置
│   │   ├── database.py          # engine/session
│   │   ├── models/              # SQLAlchemy 模型
│   │   ├── schemas/             # Pydantic v2
│   │   ├── routers/             # auth, materials, warehouses, partners,
│   │   │                        # purchase, sales, inventory, bom, mrp,
│   │   │                        # manufacturing, finance, audit
│   │   ├── services/            # 业务逻辑（状态机、库存台账、MRP、编号）
│   │   └── deps.py              # get_db / get_current_user
│   └── tests/
├── web/
│   ├── package.json / vite.config.js
│   └── src/
│       ├── api/                 # axios 封装 + 各模块 api
│       ├── stores/              # pinia（auth）
│       ├── router/              # 路由 + 登录守卫
│       ├── layout/              # 侧边菜单布局
│       └── views/               # 各模块页面 + audit 查看页
├── docker-compose.yml
├── backend.Dockerfile / web.Dockerfile / nginx.conf
├── scripts/seed.py              # 种子数据（验收基准）
└── docs/
```

## 3. 数据模型（核心表）

单据公共字段：`id, doc_no(唯一), status, remark, created_by, created_at, updated_at`。
状态枚举（字符串存储，便于审计可读）：`draft / approved / partial / completed / closed / void`。

| 表 | 关键字段 |
|---|---|
| users | username, password_hash |
| materials | code(unique), name, spec, unit, category(raw/semi/finished), default_warehouse_id |
| warehouses | code(unique), name |
| partners | code(unique), name, type(supplier/customer), contact |
| purchase_orders | partner_id, order_date, expected_date |
| purchase_order_lines | po_id, material_id, qty, unit_price, received_qty, warehouse_id |
| sales_orders | partner_id, order_date |
| sales_order_lines | so_id, material_id, qty, unit_price, shipped_qty, warehouse_id |
| inventory_moves | material_id, warehouse_id, move_type(purchase_in/sale_out/mo_issue/mo_receipt/adjust), qty(signed), unit_cost, amount, source_type, source_id, source_line_id, moved_at |
| boms | material_id(父,unique), status(active) |
| bom_lines | bom_id, material_id(子), qty_per, loss_pct |
| mrp_runs | run_date, demand_type(manual/sales), params_json, result_json |
| mrp_suggestions | run_id, material_id, type(buy/make), net_qty, converted(bool), converted_doc_id |
| manufacturing_orders | material_id, planned_qty, warehouse_id, issued_qty_cost(领料成本累计), extra_cost(人工/制费) |
| mo_issues | mo_id, material_id, qty, cost_amount |
| payables | partner_id, source_type/source_id(采购入库), amount, settled_amount, status(open/partial/settled) |
| receivables | partner_id, source_type/source_id(销售出库), amount, settled_amount, status |
| settlements | type(receipt/payment), partner_id, amount, date |
| settlement_lines | settlement_id, target_type(ap/ar), target_id, amount |
| audit_records | doc_type, doc_id, action(create/update/status/void/close), actor, field, old_value, new_value, created_at |
| operation_logs | actor, action, detail, created_at |
| doc_sequences | prefix+date(unique), last_no |

**审计实现**：单据写路径统一走 service 层，service 在同一事务内写 `audit_records`；update 逐字段 diff 出 old/new。所有查询走 `/api/v1/audit`（按 doc_type+doc_id 过滤）。

## 4. API 契约（前后端并行开发的依据）

统一前缀 `/api/v1`，JSON；分页 `?page=1&page_size=20`，响应 `{items, total}`。
错误：非 2xx 返回 `{detail: "中文错误信息"}`，前端 Element Plus message 弹出。

| 方法+路径 | 说明 |
|---|---|
| POST /auth/login | {username,password} → {token} |
| GET/POST /materials · PUT/DELETE /materials/{id} | 物料 CRUD（DELETE 仅允许无流水的） |
| GET/POST /warehouses · PUT /warehouses/{id} | 仓库 |
| GET/POST /partners?type= · PUT /partners/{id} | 往来单位 |
| GET /purchase-orders · POST /purchase-orders · GET /purchase-orders/{id} · PUT(仅 draft) · POST /{id}/approve / /receive(入库) / /void / /close | 采购 |
| GET /sales-orders … POST /{id}/approve / /ship(出库) / /void / /close | 销售 |
| GET /inventory/stock?material_id=&warehouse_id= | 现存量汇总 |
| GET /inventory/moves?material_id=&from=&to= | 台账流水 |
| GET/POST /boms/{material_id} … | BOM 查看/维护 |
| POST /mrp/run | {demand:[{material_id,qty}]} → 运算结果 |
| GET /mrp/suggestions?converted=false · POST /mrp/suggestions/convert | 建议→转 PO/MO 草稿 |
| GET/POST /manufacturing-orders … POST /{id}/release / /issue(领料) / /receive(完工入库) / /close | 工单 |
| GET /payables / /receivables · POST /settlements · GET /settlements | 核销 |
| GET /finance/statement?partner_id=&type=&from=&to= | 对账单 |
| GET /audit/records?doc_type=&doc_id= · GET /audit/logs | 审计 |

**约定**：所有列表接口支持 `?keyword=` 模糊搜索；单据详情接口返回 `lines` 与 `audit_records`（最近 20 条）内嵌。

## 5. 关键算法

- **移动加权平均**：入库时 `新均价 = (现存金额 + 入库金额) / (现存数量 + 入库数量)`；出库单价取当前均价。现存量=0 时均价清零。
- **MRP 展开**：对每个需求物料递归展开 BOM（有 BOM → make，无 BOM → buy）；`净需求 = 毛需求 - 现存量 - 在途(已审核PO未收) - 已下达MO未完工`；逐层向下传毛需求。防环：展开路径检测环路直接报错。
- **编号**：service 层 `SELECT ... FOR UPDATE` 取号，格式 `{PREFIX}{YYYYMMDD}{seq:03d}`。

## 6. 非功能

- 事务：每个写接口一个事务，审计与业务同事务提交
- 时区：全部存 UTC，前端本地展示
- 日志：api 容器 stdout 结构化日志；数据卷 `pgdata` 持久化
- 测试：后端 pytest + httpx TestClient，测试用独立 PostgreSQL（compose 里 `db-test` 或 SQLite 内存均可，选 SQLite 内存以简化 CI）；前端 v1 不要求自动化测试，手工冒烟清单代替
