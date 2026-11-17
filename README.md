# erp-lite

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

# 前端（dev 代理 /api -> VITE_API_TARGET，默认 http://localhost:8000）
cd web
npm install
VITE_API_TARGET=http://localhost:8100 npm run dev
# 访问 http://localhost:5173
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
  `ERP_ADMIN_PASSWORD`（以及 `JWT_SECRET`，默认值仅限本地调试）：

  ```bash
  echo 'JWT_SECRET=请换成长随机串
  ERP_ADMIN_PASSWORD=你的强密码' > .env
  docker compose up -d
  ```

## 种子脚本（幂等）

2 仓库（MAIN 原料仓 / FG 成品仓）、2 物料（RAW-SCREW-M3X8 螺丝 M3×8、FG-BRACKET 成品支架）、
1 客户（CUS-001）、1 供应商（SUP-001）、FG 的 BOM（2×RAW）、种子管理员。
按 code 判存在，重复执行不报错、不重复插入。

```bash
# compose 环境（db 未暴露宿主端口，拷进 api 容器执行）
docker compose cp scripts/seed.py api:/app/scripts/seed.py
docker compose exec api uv run python scripts/seed.py

# 宿主机直连数据库时
cd backend && DATABASE_URL=postgresql+psycopg://erp:erp123@localhost:5432/erp \
  uv run python ../scripts/seed.py
```

## 冒烟脚本（全链路验收）

打 `http://localhost:18080/api/v1/*`（走 nginx 反代，一并验证 web 容器），走完整业务闭环并断言数字：
登录 → 建物料/BOM → PO（100@10）分两次入库 40+60 → 查库存（100/1000/均价10）→ SO（30@15）→
MRP 运算 → 建议转单 → MO 下达 → 领料 → 完工入库（单位成本 20）→ SO 出库 → 查 FG 库存（20/400/20）→
MRP 二次运算建议转采购 → 付款核销 AP 部分 400 / 收款核销 AR → 对账单 → 审计留痕检查。
任何断言失败打印失败点并以非 0 退出码结束。

```bash
cd backend && uv run python ../scripts/smoke.py   # 需先 docker compose up 且已建种子/空库均可
```

## 备份与恢复

```bash
# 备份（pgdata volume + pg_dump 导出逻辑备份）
docker compose exec -T db pg_dump -U erp -d erp > backup_$(date +%Y%m%d).sql

# 恢复
cat backup_20260101.sql | docker compose exec -T db psql -U erp -d erp
```

## 测试

```bash
cd backend && env -u PYTHONPATH uv run pytest -q   # 125 个用例
cd web && npm run build
```
