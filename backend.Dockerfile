# erp-lite 后端镜像：uv 管理，Python 3.13（构建上下文为仓库根目录）
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 再拷贝后端代码
COPY backend/ .
RUN uv sync --frozen --no-dev

EXPOSE 8000

# 启动：先跑迁移，再起服务（venv 内命令统一走 uv run --no-sync）
CMD ["sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn erp_lite.main:app --host 0.0.0.0 --port 8000"]
