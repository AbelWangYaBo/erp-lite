"""erp-lite 后端入口：FastAPI app、路由挂接、健康检查。

lifespan 只保留常驻职责；建表由 alembic 迁移负责，一次性任务不放这里。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from erp_lite.routers import audit as audit_router
from erp_lite.routers import auth as auth_router
from erp_lite.routers import batches as batches_router
from erp_lite.routers import boms as boms_router
from erp_lite.routers import inventory as inventory_router
from erp_lite.routers import manufacturing as manufacturing_router
from erp_lite.routers import materials as materials_router
from erp_lite.routers import mrp as mrp_router
from erp_lite.routers import partners as partners_router
from erp_lite.routers import partner_certs as partner_certs_router
from erp_lite.routers import purchase as purchase_router
from erp_lite.routers import sales as sales_router
from erp_lite.routers import users as users_router
from erp_lite.routers import finance as finance_router
from erp_lite.routers import warehouses as warehouses_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表交给 alembic upgrade head（api 容器启动脚本执行）；此处无一次性任务
    yield


app = FastAPI(title="erp-lite", version="0.1.0", lifespan=lifespan)

# /api/v1/auth/login 自带鉴权豁免；audit 等业务 router 在各自定义中要求 get_current_user
app.include_router(auth_router.router)
app.include_router(audit_router.router)
app.include_router(materials_router.router)
app.include_router(warehouses_router.router)
app.include_router(partners_router.router)
app.include_router(partner_certs_router.router)
app.include_router(purchase_router.router)
app.include_router(sales_router.router)
app.include_router(inventory_router.router)
app.include_router(boms_router.router)
app.include_router(mrp_router.router)
app.include_router(manufacturing_router.router)
app.include_router(batches_router.router)
app.include_router(finance_router.router)
app.include_router(users_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
