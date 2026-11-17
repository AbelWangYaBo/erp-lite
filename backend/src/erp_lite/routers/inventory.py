"""库存查询：现存量（按物料汇总）与库存流水分页。"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user
from erp_lite.models import InventoryMove, Material, User
from erp_lite.routers.common import paginate
from erp_lite.schemas.orders import MovePage, MoveOut, StockOut
from erp_lite.services.inventory import q4, stock_by_material

router = APIRouter(
    prefix="/api/v1/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)]
)


@router.get("/stock", response_model=list[StockOut])
def list_stock(
    material_id: int | None = None,
    warehouse_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[StockOut]:
    rows = stock_by_material(db, material_id=material_id, warehouse_id=warehouse_id)
    materials = {
        m.id: m
        for m in db.scalars(
            select(Material).where(
                Material.id.in_([r.material_id for r in rows]) if rows else select(Material).where(False)
            )
        ).all()
    }
    out = []
    for r in rows:
        mat = materials.get(r.material_id)
        avg = q4(r.amount / r.qty) if r.qty > 0 else None
        out.append(
            StockOut(
                material_id=r.material_id,
                material_code=mat.code if mat else "",
                material_name=mat.name if mat else "",
                qty=r.qty,
                avg_cost=avg,
                amount=r.amount,
            )
        )
    return out


@router.get("/moves", response_model=MovePage)
def list_moves(
    material_id: int | None = None,
    warehouse_id: int | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(InventoryMove).order_by(
        InventoryMove.moved_at.desc(), InventoryMove.id.desc()
    )
    if material_id is not None:
        stmt = stmt.where(InventoryMove.material_id == material_id)
    if warehouse_id is not None:
        stmt = stmt.where(InventoryMove.warehouse_id == warehouse_id)
    if from_ is not None:
        stmt = stmt.where(InventoryMove.moved_at >= from_)
    if to is not None:
        stmt = stmt.where(InventoryMove.moved_at <= to)
    items, total = paginate(db, stmt, page, page_size)
    return {"items": [MoveOut.model_validate(m).model_dump(mode="json") for m in items], "total": total}
