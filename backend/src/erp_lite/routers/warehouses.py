"""仓库 CRUD：唯一码校验、keyword 模糊搜索、审计留痕（v1 不提供删除）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import User, Warehouse
from erp_lite.routers.common import apply_update_with_diff_audit, paginate
from erp_lite.schemas import (
    WarehouseCreate,
    WarehouseOut,
    WarehousePage,
    WarehouseUpdate,
)
from erp_lite.services.audit import write_audit, write_op_log

router = APIRouter(
    prefix="/api/v1/warehouses", tags=["warehouses"], dependencies=[Depends(get_current_user)]
)

DOC_TYPE = "warehouse"


def _get_or_404(db: Session, warehouse_id: int) -> Warehouse:
    obj = db.get(Warehouse, warehouse_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="仓库不存在")
    return obj


def _ensure_code_free(db: Session, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Warehouse).where(Warehouse.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Warehouse.id != exclude_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仓库编码已存在")


@router.get("", response_model=WarehousePage)
def list_warehouses(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Warehouse).order_by(Warehouse.id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Warehouse.code.ilike(like), Warehouse.name.ilike(like)))
    items, total = paginate(db, stmt, page, page_size)
    return {"items": items, "total": total}


@router.get("/{warehouse_id}", response_model=WarehouseOut)
def get_warehouse(
    warehouse_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Warehouse:
    return _get_or_404(db, warehouse_id)


@router.post("", response_model=WarehouseOut)
def create_warehouse(
    payload: WarehouseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Warehouse:
    _ensure_code_free(db, payload.code)
    obj = Warehouse(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(db, DOC_TYPE, obj.id, "create", current_user.username)
    write_op_log(db, current_user.username, "warehouse_create", f"新增仓库：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Warehouse:
    obj = _get_or_404(db, warehouse_id)
    if payload.code != obj.code:
        _ensure_code_free(db, payload.code, exclude_id=obj.id)
    apply_update_with_diff_audit(db, obj, payload, DOC_TYPE, current_user)
    write_op_log(db, current_user.username, "warehouse_update", f"修改仓库：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj
