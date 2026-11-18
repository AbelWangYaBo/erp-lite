"""批次台账查询（V2-S2 只读；v3 双段检验 inspect/judge + 处置闭环 + 追溯）。

权限：全员可查（batch.trace，八角色均持有），GMP 记录可及性。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import ensure_not_self, get_current_user, require_action
from erp_lite.models import Batch, InspectionRecord, InspStatus, PurchaseOrder, User
from erp_lite.routers.common import paginate
from erp_lite.schemas.batches import (
    BatchDetail,
    BatchOut,
    BatchPage,
    DisposeIn,
    InspectIn,
    InspectionRecordOut,
    JudgeIn,
    TraceOut,
)
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.batches import (
    dispose_close,
    get_batch_stock,
    judge_batch,
    record_inspection,
    scrap_batch,
    trace_backward,
    trace_forward,
)
from erp_lite.services.inventory import get_batch_avg_cost
from erp_lite.services.returns import create_purchase_return

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


@router.get("/{batch_id}/inspections", response_model=list[InspectionRecordOut])
def list_inspections(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[InspectionRecordOut]:
    """批次检验记录历史（含检项明细/建议结论/判定人，v3 补检项可查断点）。"""
    _get_or_404(db, batch_id)
    records = db.scalars(
        select(InspectionRecord)
        .where(InspectionRecord.batch_id == batch_id)
        .order_by(InspectionRecord.id.desc())
    ).all()
    return [InspectionRecordOut.model_validate(r) for r in records]


@router.post(
    "/{batch_id}/inspect",
    response_model=InspectionRecordOut,
    dependencies=[Depends(require_action("batch.inspect"))],
)
def inspect_batch_api(
    batch_id: int,
    body: InspectIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InspectionRecord:
    """qc 段：录入检项与建议结论（批次保持 pending，等待 qa 复核判定）。"""
    batch = _get_or_404(db, batch_id)
    try:
        record = record_inspection(
            db,
            batch,
            items=[item.model_dump() for item in body.items],
            suggest_conclusion=body.suggest_conclusion,
            recorder=current_user.username,
            remark=body.remark,
        )
    except ValueError as exc:
        raise _valueerror_400(exc)
    write_audit(
        db, "batch", batch.id, "inspection", current_user.username,
        role=current_user.role, doc_no=batch.batch_no,
        field="inspection_record", new=f"录入建议：{body.suggest_conclusion}",
    )
    write_op_log(
        db, current_user.username, "batch_inspect",
        f"检验录入：批次 {batch.batch_no}，建议结论 {body.suggest_conclusion}，待 qa 判定",
        doc_type="batch", doc_id=batch.id,
    )
    db.commit()
    db.refresh(record)
    return record


@router.post(
    "/{batch_id}/judge",
    response_model=BatchOut,
    dependencies=[Depends(require_action("batch.judge"))],
)
def judge_batch_api(
    batch_id: int,
    body: JudgeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Batch:
    """qa 段：复核判定 pass → qualified / fail → rejected（不得自审，操作人≠qc 录入人）。"""
    batch = _get_or_404(db, batch_id)
    pending_rec = db.scalar(
        select(InspectionRecord)
        .where(
            InspectionRecord.batch_id == batch.id,
            InspectionRecord.conclusion.is_(None),
        )
        .order_by(InspectionRecord.id.desc())
    )
    ensure_not_self(current_user, pending_rec.recorder if pending_rec else None, "检验复核判定")
    old_status = batch.insp_status.value
    try:
        judge_batch(db, batch, conclusion=body.conclusion, judge_by=current_user.username)
    except ValueError as exc:
        raise _valueerror_400(exc)
    new_status = batch.insp_status.value
    write_audit(
        db, "batch", batch.id, "judge", current_user.username,
        role=current_user.role, doc_no=batch.batch_no,
        field="insp_status", old=old_status, new=new_status,
    )
    write_op_log(
        db, current_user.username, "batch_judge",
        f"复核判定：批次 {batch.batch_no} {old_status} → {new_status}（{body.conclusion}）",
        doc_type="batch", doc_id=batch.id,
    )
    db.commit()
    db.refresh(batch)
    return batch


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
    """不合格批次处置（v3 账务闭环）：

    - return：生成采购退货单（红字出库+冲未清应付+冲 PO 已收量，completed 回 partial）
    - destroy：报废流水清批次现存 + loss_amount 留损失
    - 均 → insp_status=closed；成品/半成品批次仅支持 destroy。
    """
    batch = _get_or_404(db, batch_id)
    old_status = batch.insp_status.value
    ret_doc_no: str | None = None
    try:
        if body.disposition == "return":
            po = db.get(PurchaseOrder, batch.source_id)
            if po is None:
                raise ValueError("批次来源采购订单不存在，无法生成退货单")
            ret = create_purchase_return(
                db, batch=batch, po=po, reason=body.remark, actor=current_user.username
            )
            ret_doc_no = ret.doc_no
        else:
            loss = scrap_batch(db, batch, actor=current_user.username)
        dispose_close(batch, disposition=body.disposition, remark=body.remark)
    except ValueError as exc:
        raise _valueerror_400(exc)
    write_audit(
        db, "batch", batch.id, "dispose", current_user.username,
        role=current_user.role, doc_no=batch.batch_no,
        field="insp_status", old=old_status, new=InspStatus.closed.value,
    )
    write_op_log(
        db, current_user.username, "batch_dispose",
        (
            f"不合格处置：批次 {batch.batch_no} 退货 {ret_doc_no}，已冲应付/已收量"
            if ret_doc_no
            else f"不合格处置：批次 {batch.batch_no} 报废，冲减现存金额 {loss}，状态 → closed"
        ),
        doc_type="batch", doc_id=batch.id,
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
