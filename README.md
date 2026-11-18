# erp-lite

> 项目介绍页（GitHub Pages 静态页：定位 / 闭环链路 / 权限矩阵 / 本地运行指引；ERP 业务界面依赖后端，不在线上运行）：
> **https://abelwangyabo.github.io/erp-lite/**（页面源码在 `pages/`，由 `.github/workflows/pages.yml` 自动部署）

小型进销存 + MRP 一体化 ERP（Vue3 + FastAPI + PostgreSQL）。

## 目录结构

- `backend/` — FastAPI + SQLAlchemy 2.0 + Alembic（uv 管理，Python 3.13）
- `web/` — Vue 3 + Vite + Element Plus + Pinia
- `docker-compose.yml` — 三服务编排：`db` / `api` / `web`
- `scripts/` — 种子脚本 `seed.py` 与冒烟脚本 `smoke.py`
- `docs/` — PRD、设计与计划文档

## 本地开发

> 本地开发不使用 docker（docker 仅用于服务端部署）。本机 8000 端口被其他服务占用，示例用 8100。

```bash
# 后端（默认 SQLite；首次需先建表）
cd backend
uv sync
export DATABASE_URL=sqlite:///./erp-dev.db
uv run alembic upgrade head
uv run python ../scripts/seed.py
uv run uvicorn erp_lite.main:app --reload --port 8100

# 前端（dev 代理 /api -> VITE_API_TARGET，默认 http://localhost:8100）
cd web
npm install
npm run dev
# 访问 http://localhost:15173（5173 留给字典应用，strictPort 被占时启动报错不会漂移）
```

## Docker 一键启动

```bash
docker compose up -d --build
docker compose ps        # 三服务 Up/healthy
```

- 访问 **http://localhost:18080**（nginx 托管前端静态资源，`/api` 反代到 api 容器；api 容器启动时自动执行 `alembic upgrade head`）。
- 端口说明：本机 8080 常被其他服务占用，web 映射为 **18080:80**，可在 `docker-compose.yml` 里改回。
- 数据库 `db` 容器**不暴露宿主端口**（避免与本机既有 PostgreSQL 冲突），仅容器网络内可访问。
- 默认管理员账号 `admin / admin123`。**部署到公网前务必改密码**：在 `.env` 或环境变量中设置
  `JWT_SECRET`，并为 `admin` 改强密码（用户管理接口仅 admin 可用）：

  ```bash
  echo 'JWT_SECRET=请换成长随机串' > .env
  docker compose up -d
  ```

- **v3 演示八账号**（seed 脚本创建，账号名即角色名，密码错开避免同密风险）：

  | 账号 | 密码 | 角色 |
  |---|---|---|
  | admin | admin123 | admin（全量兜底审核） |
  | purchase | p123 | purchase（PO 建/审、资质录入） |
  | sales | s123 | sales（SO 建/审、销售退货） |
  | warehouse | w123 | warehouse（收货/出库/建盘点） |
  | production | p123456 | production（BOM/MRP/MO/领退料） |
  | qc | qc123 | qc（检验记录录入） |
  | qa | q123 | qa（复核判定、处置、资质审核/撤销） |
  | finance | f123 | finance（核销/冲回/对账/盘点过账） |

  注意：角色变更**即时生效**（鉴权以数据库中的当前角色为准，JWT 中的 role claim
  仅作留痕，无需重新登录；前端菜单在下次页面跳转时自动刷新）。
  **审核类动作一律不得自审**（操作人 ≠ 单据创建/录入人，admin 无豁免）——
  这是 v3 的职责分离硬约束：purchase 建的 PO 由 admin（或另一采购员）审核。

## 种子脚本（幂等）

2 仓库（MAIN 原料仓 / FG 成品仓）、3 批次物料（RAW-SCREW-M3X8 螺丝 M3×8、
SEMI-BRACKET 支架半成品、FG-BRACKET 支架成品，均 `batch_required=true`，成品带
UDI-DI）、两层 BOM（FG→1×SEMI→2×RAW）、1 客户（CUS-001）、3 供应商（SUP-001 资质
有效 / SUP-002 临期 +10 天 / SUP-003 已过期，各挂一张 audited 证照）、演示八账号
（见上表）。按 code/cert_no 判存在，重复执行不报错、不重复插入。

```bash
# compose 环境（db 未暴露宿主端口，拷进 api 容器执行）
docker compose cp scripts/seed.py api:/app/scripts/seed.py
docker compose exec api uv run python scripts/seed.py

# 宿主机直连数据库时
cd backend && DATABASE_URL=postgresql+psycopg://erp:erp123@localhost:5432/erp \
  uv run python ../scripts/seed.py
```

## 冒烟脚本（全链路验收）

> 注意：`smoke.py` 与 `smoke_v2.py` 均为**非幂等**脚本（会真实产生单据/流水且断言
> 具体数字），必须在 seed 后的**干净库**上运行，**不可重复执行**；重跑前需删库重建
> （`rm erp-dev.db` → `alembic upgrade head` → `seed.py`）。

打 `http://localhost:18080/api/v1/*`（走 nginx 反代，一并验证 web 容器），走完整业务闭环并断言数字：
登录 → 建物料/BOM → PO（100@10）分两次入库 40+60 → 查库存（100/1000/均价10）→ SO（30@15）→
MRP 运算 → 建议转单 → MO 下达 → 领料 → 完工入库（单位成本 20）→ SO 出库 → 查 FG 库存（20/400/20）→
MRP 二次运算建议转采购 → 付款核销 AP 部分 400 / 收款核销 AR → 对账单 → 审计留痕检查。
任何断言失败打印失败点并以非 0 退出码结束。

```bash
cd backend && uv run python ../scripts/smoke.py   # 需先 docker compose up 且已建种子/空库均可
# 本地开发（无 docker）直连 8100 时：
cd backend && SMOKE_BASE=http://localhost:8100 uv run python ../scripts/smoke.py
```

## v3 演示链路（八角色接力，全链路闭环）

seed 后即可用八账号手工走通（或直接跑 `smoke_v3.py` 全自动验证）。设计见
docs/DESIGN-v3.md，一句话流程：

> 登录后各角色在**工作台**（首页）看到自己的待办 → **purchase** 向 SUP-001 下 PO →
> **admin** 审核（不得自审）→ **warehouse** 收货（批次 `pending`，待检量在库存页独立
> 展示）→ **qc** 录入检验记录 → **qa** 复核放行（`qualified`）/ 判不合格 → **qa**
> 处置：退货=生成采购退货单+冲应付+冲已收量；报废=清库存+记损失 → **production** MRP
> → make 转单 → admin 下达 → 按合格批次领料（可退回）→ 完工入库（成品批次 `pending`，
> 同走 qc/qa 放行）→ **sales** 建 SO → admin 审核 → **warehouse** 出库（退货走销售
> 退货单：批次回待检重检+冲应收）→ **finance** 核销（可冲回）、对账（期初+新增−核销=
> 期末）、盘点过账 → 全程单据可点击互跳、审计带角色可过滤。

合规控制点随手可验：

- 越权 403：warehouse 建 PO、qc 复核判定、qa 检验录入、finance 建 PO 均被拒；
- 自审拦截 400：purchase 自审 PO、qc 自判检验、qa 撤自己审的资质均被拒（规则 A）；
- 资质拦截 400：SUP-003（过期）下单被拦，MRP 转单同样被拦（旁路已堵），
  撤销（revoked）资质立即失效，SUP-002（临期）进预警列表；
- 放行拦截 400：pending/rejected 批次不能领料、不能出库；库存三口径（合格/待检/
  不合格）让"看到有货发不出"有解释；
- 处置/退货/报废/盘点全部产生真实账务联动（冲应付/冲应收/调整流水），无"打标签式"闭环；
- 正反向追溯全程带单据链接；审计带角色与单据号，可按人/时间过滤。

```bash
# smoke_v2 默认打 http://localhost:8100（本地开发后端）
cd backend && uv run python ../scripts/smoke_v2.py   # 五角色全链路 + 越权/合规拦截 + 追溯断言
```

## GMP 控制点说明（定位声明）

v2 将《医疗器械生产质量管理规范》中信息化系统最相关的五个控制点落成系统能力：
**批次追溯**（批次台账 + 正反向追溯 API）、**职责分离**（RBAC 五角色 + 全操作审计带
角色）、**放行受控**（检验合格方可领料/出库，MRP 可用量只计合格批次）、**资质受控**
（供应商证照有效期拦截 + 临期预警）、**标识唯一**（成品 UDI-DI + PI=批号随出库留痕）。
**本系统仅为个人自用演示/参考实现，系统能力落地参考，不构成任何 GMP 合规认证**；
法规定性口径归法规/RA，系统只保证「控制点可执行、证据可留存」。

## 备份与恢复

```bash
# 备份（pgdata volume + pg_dump 导出逻辑备份）
docker compose exec -T db pg_dump -U erp -d erp > backup_$(date +%Y%m%d).sql

# 恢复
cat backup_20260101.sql | docker compose exec -T db psql -U erp -d erp
```

## 测试

```bash
cd backend && env -u PYTHONPATH uv run pytest -q   # 256 用例全绿（v1 进销存 + v2 批次/检验/资质/RBAC）
cd web && npm run build
```
