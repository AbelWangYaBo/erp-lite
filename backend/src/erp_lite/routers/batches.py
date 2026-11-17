"""批次台账查询（V2-S2 只读；S3 加 inspect/dispose/追溯）。

权限：全员可查（batch.trace，五个演示角色均持有），GMP 记录可及性。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import Batch, InspStatus, User
from erp_lite.routers.common import paginate
from erp_lite.schemas.batches import BatchDetail, BatchOut, BatchPage
from erp_lite.services.batches import get_batch_stock
from erp_lite.services.inventory import get_batch_avg_cost

router = APIRouter(
    prefix="/api/v1/batches",
    tags=["batches"],
    dependencies=[Depends(get_current_user), Depends(require_action("batch.trace"))],
)


def _get_or_404(db: Session, batch_id: int) -> Batch:
    obj = db.get(Batch, batch_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="批次不存在")
    return obj


@router.get("", response_model=BatchPage)
def list_batches(
    material_id: int | None = None,
    insp_status: InspStatus | None = None,
    keyword: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Batch).order_by(Batch.id)
    if material_id is not None:
        stmt = stmt.where(Batch.material_id == material_id)
    if insp_status is not None:
        stmt = stmt.where(Batch.insp_status == insp_status)
    if keyword:
        stmt = stmt.where(Batch.batch_no.ilike(f"%{keyword}%"))
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [BatchOut.model_validate(b).model_dump(mode="json") for b in items],
        "total": total,
    }


@router.get("/{batch_id}", response_model=BatchDetail)
def get_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BatchDetail:
    batch = _get_or_404(db, batch_id)
    qty, amount = get_batch_stock(db, batch.id)
    detail = BatchDetail(
        **BatchOut.model_validate(batch).model_dump(),
        stock_qty=qty,
        stock_amount=amount,
        avg_cost=get_batch_avg_cost(db, batch.id),
        material_code=batch.material.code,
        material_name=batch.material.name,
    )
    return detail
