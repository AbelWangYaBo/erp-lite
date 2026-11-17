"""物料 CRUD：唯一码校验、keyword 模糊搜索、库存流水禁删、审计留痕。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import InventoryMove, Material, User
from erp_lite.routers.common import apply_update_with_diff_audit, paginate
from erp_lite.schemas import (
    MaterialCreate,
    MaterialOut,
    MaterialPage,
    MaterialUpdate,
)
from erp_lite.services.audit import write_audit, write_op_log

router = APIRouter(
    prefix="/api/v1/materials", tags=["materials"], dependencies=[Depends(get_current_user)]
)

DOC_TYPE = "material"


def _get_or_404(db: Session, material_id: int) -> Material:
    obj = db.get(Material, material_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="物料不存在")
    return obj


def _ensure_code_free(db: Session, code: str, exclude_id: int | None = None) -> None:
    stmt = select(Material).where(Material.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Material.id != exclude_id)
    if db.scalar(stmt.limit(1)) is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="物料编码已存在")


@router.get("", response_model=MaterialPage)
def list_materials(
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Material).order_by(Material.id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(or_(Material.code.ilike(like), Material.name.ilike(like)))
    items, total = paginate(db, stmt, page, page_size)
    return {"items": items, "total": total}


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(
    material_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Material:
    return _get_or_404(db, material_id)


@router.post("", response_model=MaterialOut)
def create_material(
    payload: MaterialCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Material:
    _ensure_code_free(db, payload.code)
    obj = Material(**payload.model_dump())
    db.add(obj)
    db.flush()
    write_audit(db, DOC_TYPE, obj.id, "create", current_user.username)
    write_op_log(db, current_user.username, "material_create", f"新增物料：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj


@router.put("/{material_id}", response_model=MaterialOut)
def update_material(
    material_id: int,
    payload: MaterialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Material:
    obj = _get_or_404(db, material_id)
    if payload.code != obj.code:
        _ensure_code_free(db, payload.code, exclude_id=obj.id)
    apply_update_with_diff_audit(db, obj, payload, DOC_TYPE, current_user)
    write_op_log(db, current_user.username, "material_update", f"修改物料：{obj.code}")
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{material_id}")
def delete_material(
    material_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    obj = _get_or_404(db, material_id)
    has_moves = db.scalar(
        select(InventoryMove.id).where(InventoryMove.material_id == material_id).limit(1)
    )
    if has_moves is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该物料已有库存流水，禁止删除",
        )
    write_audit(db, DOC_TYPE, obj.id, "delete", current_user.username)
    write_op_log(db, current_user.username, "material_delete", f"删除物料：{obj.code}")
    db.delete(obj)
    db.commit()
    return {"ok": True}
