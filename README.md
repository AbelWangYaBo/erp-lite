# erp-lite

小型进销存 + MRP 一体化 ERP（Vue3 + FastAPI + PostgreSQL）。

## 目录结构

- `backend/` — FastAPI + SQLAlchemy 2.0 + Alembic（uv 管理，Python 3.13）
- `web/` — Vue 3 + Vite + Element Plus + Pinia
- `docker-compose.yml` — 三服务编排：`db` / `api` / `web`
- `docs/` — PRD、设计与计划文档

## 本地开发

```bash
# 后端（默认 SQLite；正式跑配 DATABASE_URL 指向 PostgreSQL）
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn erp_lite.main:app --reload --port 8000

# 前端（dev 代理 /api -> http://localhost:8000）
cd web
npm install
npm run dev
```

## Docker 一键启动

```bash
docker compose up -d --build
```

启动后访问 http://localhost:8080（nginx 托管前端静态资源，`/api` 反代到 api 容器；api 容器启动时自动执行 `alembic upgrade head`）。数据库数据持久化在 `pgdata` volume。

默认管理员账号 `admin / admin123`（仅限本地，通过 `ERP_ADMIN_USER/ERP_ADMIN_PASSWORD` 环境变量覆盖）。
