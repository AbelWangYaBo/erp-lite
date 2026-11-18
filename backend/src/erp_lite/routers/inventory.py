"""库存查询：现存量（三口径）与库存流水分页 + 盘点单（v3）。"""

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import (
    Batch,
    InspStatus,
    InventoryMove,
    Material,
    StockCount,
    StockCountLine,
    User,
    Warehouse,
)
from erp_lite.routers.common import paginate
from erp_lite.schemas.orders import MovePage, MoveOut, StockOut
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.batches import get_available
from erp_lite.services.inventory import get_stock, q4, stock_by_material
from erp_lite.services.numbering import next_doc_no
from erp_lite.services.stockcount import post_stock_count, snapshot_warehouse

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
    """现存量（v3 三口径）：批次物料拆 合格/待检/不合格 三列——仓管看到的
    「有货」永远能解释「为什么发不了」。非批次物料三口径同 qty。"""
    rows = stock_by_material(db, material_id=material_id, warehouse_id=warehouse_id)
    materials = {
        m.id: m
        for m in db.scalars(
            select(Material).where(
                Material.id.in_([r.material_id for r in rows]) if rows else select(Material).where(False)
            )
        ).all()
    }
    batch_mats = {mid for mid, m in materials.items() if m.batch_required}
    out = []
    for r in rows:
        mat = materials.get(r.material_id)
        avg = q4(r.amount / r.qty) if r.qty > 0 else None
        qualified = pending = rejected = None
        if r.material_id in batch_mats:
            qualified = get_available(db, r.material_id, warehouse_id, only_qualified=True)
            stmt = select(Batch.id).where(
                Batch.material_id == r.material_id,
                Batch.insp_status == InspStatus.pending,
            )
            pending = sum(
                (get_stock(db, r.material_id, warehouse_id, batch_id=bid)[0] for bid in db.scalars(stmt)),
                q4(0),
            )
            stmt2 = select(Batch.id).where(
                Batch.material_id == r.material_id,
                Batch.insp_status == InspStatus.rejected,
            )
            rejected = sum(
                (get_stock(db, r.material_id, warehouse_id, batch_id=bid)[0] for bid in db.scalars(stmt2)),
                q4(0),
            )
        out.append(
            StockOut(
                material_id=r.material_id,
                material_code=mat.code if mat else "",
                material_name=mat.name if mat else "",
                qty=r.qty,
                avg_cost=avg,
                amount=r.amount,
                qualified_qty=qualified,
                pending_qty=pending,
                rejected_qty=rejected,
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
    out = []
    for m in items:
        d = MoveOut.model_validate(m).model_dump(mode="json")
        if m.batch_id is not None:
            batch = db.get(Batch, m.batch_id)
            d["batch_no"] = batch.batch_no if batch is not None else None
        out.append(d)
    return {"items": out, "total": total}


# ---------- v3：盘点单 ----------


class StockCountLineIn(BaseModel):
    line_id: int
    counted_qty: Decimal = Field(ge=0)


class StockCountCreate(BaseModel):
    warehouse_id: int
    remark: str | None = Field(default=None, max_length=255)


class StockCountUpdate(BaseModel):
    lines: list[StockCountLineIn] = Field(min_length=1)


class StockCountLineOut(BaseModel):
    id: int
    material_id: int
    batch_id: int | None
    system_qty: float
    counted_qty: float | None
    diff_qty: float

    model_config = {"from_attributes": True}


class StockCountOut(BaseModel):
    id: int
    doc_no: str
    warehouse_id: int
    status: str
    remark: str | None
    created_by: str | None
    created_at: datetime
    posted_by: str | None
    posted_at: datetime | None
    lines: list[StockCountLineOut]

    model_config = {"from_attributes": True}


@router.post(
    "/stock-counts",
    dependencies=[Depends(require_action("stockcount.create"))],
)
def create_stock_count(
    body: StockCountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """建盘点单：按仓库账面自动快照全部 (物料, 批次) 组合为行。"""
    if db.get(Warehouse, body.warehouse_id) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仓库不存在")
    doc_no = next_doc_no(db, "SC")
    count = StockCount(
        doc_no=doc_no,
        warehouse_id=body.warehouse_id,
        remark=body.remark,
        created_by=current_user.username,
        lines=snapshot_warehouse(db, body.warehouse_id),
    )
    db.add(count)
    db.flush()
    write_audit(
        db, "stock_count", count.id, "create", current_user.username,
        role=current_user.role, doc_no=count.doc_no,
        field="lines", new=str(len(count.lines)),
    )
    write_op_log(
        db, current_user.username, "stock_count_create",
        f"建盘点单：{count.doc_no}（{len(count.lines)} 行账面快照），待录实盘",
        doc_type="stock_count", doc_id=count.id,
    )
    db.commit()
    db.refresh(count)
    return StockCountOut.model_validate(count).model_dump(mode="json")


@router.get("/stock-counts")
def list_stock_counts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(StockCount).order_by(StockCount.id.desc())
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [StockCountOut.model_validate(c).model_dump(mode="json") for c in items],
        "total": total,
    }


@router.get("/stock-counts/{count_id}")
def get_stock_count(
    count_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    count = db.get(StockCount, count_id)
    if count is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在")
    return StockCountOut.model_validate(count).model_dump(mode="json")


@router.put(
    "/stock-counts/{count_id}",
    dependencies=[Depends(require_action("stockcount.create"))],
)
def update_stock_count(
    count_id: int,
    body: StockCountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """录实盘（draft 状态）：按行更新 counted_qty。"""
    count = db.get(StockCount, count_id)
    if count is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在")
    if count.status != "draft":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅草稿盘点单可录实盘")
    line_map = {l.id: l for l in count.lines}
    for item in body.lines:
        line = line_map.get(item.line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"盘点行不属于该单：{item.line_id}",
            )
        line.counted_qty = q4(item.counted_qty)
    write_audit(
        db, "stock_count", count.id, "update", current_user.username,
        role=current_user.role, doc_no=count.doc_no,
        field="counted_lines", new=str(len(body.lines)),
    )
    write_op_log(
        db, current_user.username, "stock_count_update",
        f"录实盘：{count.doc_no} {len(body.lines)} 行，待 finance 过账",
        doc_type="stock_count", doc_id=count.id,
    )
    db.commit()
    db.refresh(count)
    return StockCountOut.model_validate(count).model_dump(mode="json")


@router.post(
    "/stock-counts/{count_id}/post",
    dependencies=[Depends(require_action("stockcount.post"))],
)
def post_stock_count_api(
    count_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """过账：非零差异行生成带批次 adjust 流水（账实差异修正），posted 后不可改。"""
    count = db.get(StockCount, count_id)
    if count is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="盘点单不存在")
    if count.status != "draft":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="盘点单已过账")
    try:
        diff_lines, moved_amount = post_stock_count(db, count, posted_by=current_user.username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    write_audit(
        db, "stock_count", count.id, "post", current_user.username,
        role=current_user.role, doc_no=count.doc_no,
        field="status", old="draft", new="posted",
    )
    write_op_log(
        db, current_user.username, "stock_count_post",
        f"盘点过账：{count.doc_no} 差异 {diff_lines} 行，调整金额 |{moved_amount}|",
        doc_type="stock_count", doc_id=count.id,
    )
    db.commit()
    db.refresh(count)
    return StockCountOut.model_validate(count).model_dump(mode="json")
