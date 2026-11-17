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
from erp_lite.schemas.batches import (
    BatchDetail,
    BatchOut,
    BatchPage,
    DisposeIn,
    InspectIn,
    InspectionRecordOut,
    TraceOut,
)
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.batches import (
    dispose_batch,
    get_batch_stock,
    inspect_batch,
    trace_backward,
    trace_forward,
)
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


def _valueerror_400(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post(
    "/{batch_id}/inspect",
    response_model=InspectionRecordOut,
    dependencies=[Depends(require_action("inspect.judge"))],
)
def inspect_batch_api(
    batch_id: int,
    body: InspectIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """QA 检验判定：pass → qualified / fail → rejected，检验记录+审计留痕。"""
    batch = _get_or_404(db, batch_id)
    old_status = batch.insp_status.value
    try:
        record = inspect_batch(
            db,
            batch,
            items=[item.model_dump() for item in body.items],
            conclusion=body.conclusion,
            inspector=current_user.username,
            remark=body.remark,
        )
    except ValueError as exc:
        raise _valueerror_400(exc)
    new_status = batch.insp_status.value
    write_audit(
        db, "batch", batch.id, "inspection", current_user.username,
        field="insp_status", old=old_status, new=new_status,
    )
    write_op_log(
        db, current_user.username, "batch_inspect",
        f"检验判定：批次 {batch.batch_no} {old_status} → {new_status}（{body.conclusion}）",
    )
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "batch_id": record.batch_id,
        "items": record.items_json,
        "conclusion": record.conclusion,
        "inspector": record.inspector,
        "inspected_at": record.inspected_at,
        "remark": record.remark,
    }


@router.post(
    "/{batch_id}/dispose",
    response_model=BatchOut,
    dependencies=[Depends(require_action("batch.dispose"))],
)
def dispose_batch_api(
    batch_id: int,
    body: DisposeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Batch:
    """不合格批次处置：return 退货 / destroy 销毁 → insp_status=closed 留痕。"""
    batch = _get_or_404(db, batch_id)
    try:
        dispose_batch(db, batch, disposition=body.disposition, remark=body.remark)
    except ValueError as exc:
        raise _valueerror_400(exc)
    write_audit(
        db, "batch", batch.id, "dispose", current_user.username,
        field="insp_status", old=InspStatus.rejected.value, new=InspStatus.closed.value,
    )
    write_op_log(
        db, current_user.username, "batch_dispose",
        f"不合格处置：批次 {batch.batch_no} {body.disposition}，状态 → closed",
    )
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/{batch_id}/trace-forward", response_model=TraceOut)
def trace_forward_api(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """正向追溯：成品批次 → 所耗原料/子件批次 + 供应商（全员可查）。"""
    batch = _get_or_404(db, batch_id)
    try:
        return trace_forward(db, batch)
    except ValueError as exc:
        raise _valueerror_400(exc)


@router.get("/{batch_id}/trace-backward", response_model=TraceOut)
def trace_backward_api(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """反向召回：原料批次 → 流向的 MO → 成品批次（全员可查）。"""
    batch = _get_or_404(db, batch_id)
    try:
        return trace_backward(db, batch)
    except ValueError as exc:
        raise _valueerror_400(exc)
