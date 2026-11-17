"""批次服务（V2-S2 批次内核）：批次创建/复用、批次级现存与均价、可用量口径。

- 批次号全表唯一；同批次号再入库复用原记录（须同物料，异物料 400）。
- 采购入库批次 insp_status=pending（待检）；MO 完工批次 S2 直接建为
  qualified（S3 接入 QA 检验判定后改为 pending→QA 判 qualified）。
- 可用量 = Σ qualified 批次现存；S2 默认口径含 pending（only_qualified=False），
  qualified 过滤已实现并挂开关，S3 接检验判定时把出/领料校验切到
  only_qualified=True 即可，无需返工。
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import Batch, InspStatus
from erp_lite.services.inventory import get_stock, q4


def get_or_create_batch(
    db: Session,
    *,
    batch_no: str,
    material_id: int,
    source_type: str,
    source_id: int,
    supplier_id: int | None = None,
    insp_status: InspStatus = InspStatus.pending,
    created_by: str | None = None,
) -> tuple[Batch, bool]:
    """按批次号取批次；不存在则创建，存在则复用（校验同物料）。

    返回 (批次, 是否新建)。复用时保留原来源/供应商/检验状态。
    """
    batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
    if batch is not None:
        if batch.material_id != material_id:
            raise ValueError(
                f"批次号 {batch_no} 已属于物料 {batch.material_id}，"
                f"不能用于物料 {material_id}"
            )
        return batch, False
    batch = Batch(
        batch_no=batch_no,
        material_id=material_id,
        source_type=source_type,
        source_id=source_id,
        supplier_id=supplier_id,
        insp_status=insp_status,
        created_by=created_by,
    )
    db.add(batch)
    db.flush()
    return batch, True


def next_finished_batch_no(db: Session, mo_id: int, mo_doc_no: str) -> str:
    """成品批次号 = MO 单号 + '-' + 序号（按该 MO 已生成批次数递增）。"""
    count = len(
        db.scalars(
            select(Batch.id).where(
                Batch.source_type == "manufacturing_order",
                Batch.source_id == mo_id,
            )
        ).all()
    )
    return f"{mo_doc_no}-{count + 1}"


def get_batch_stock(
    db: Session, batch_id: int, warehouse_id: int | None = None
) -> tuple[Decimal, Decimal]:
    """批次现存量 / 现存金额 = 该批次流水汇总（入库正、出库负）。"""
    stmt = select(Batch.material_id).where(Batch.id == batch_id)
    material_id = db.scalar(stmt)
    if material_id is None:
        return q4(0), q4(0)
    return get_stock(db, material_id, warehouse_id, batch_id=batch_id)


def get_available(
    db: Session,
    material_id: int,
    warehouse_id: int | None = None,
    only_qualified: bool = False,
) -> Decimal:
    """可用量 = Σ 批次现存（only_qualified=True 时只计 qualified 批次）。

    S2 口径：默认含 pending（物理在库即可见）；S3 接入检验放行后，
    领料/出库校验与 MRP 可用量切 only_qualified=True。
    """
    stmt = select(Batch.id).where(Batch.material_id == material_id)
    if only_qualified:
        stmt = stmt.where(Batch.insp_status == InspStatus.qualified)
    total = Decimal("0")
    for batch_id in db.scalars(stmt).all():
        qty, _ = get_stock(db, material_id, warehouse_id, batch_id=batch_id)
        total = q4(total + qty)
    return total
