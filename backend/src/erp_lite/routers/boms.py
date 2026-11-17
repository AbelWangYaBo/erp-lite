"""BOM 维护：查看 / 整体替换（多级允许、环路检测、用量校验）。"""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import Bom, BomLine, Material, User
from erp_lite.schemas.production import BomOut, BomPut
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.inventory import _d
from erp_lite.services.mrp import get_active_bom

router = APIRouter(
    prefix="/api/v1/boms",
    tags=["boms"],
    dependencies=[Depends(get_current_user)],
)

DOC_TYPE = "bom"


def _check_cycle(db: Session, material_id: int, new_lines) -> None:
    """环路检测：沿子件→其 BOM→子件… 的链路，任何回到路径上的物料即成环（含间接环）。"""
    child_ids = [line.material_id for line in new_lines]
    if material_id in child_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"BOM 成环：物料 {material_id} 不能直接作为自己的子件",
        )

    def walk(mid: int, path: tuple[int, ...]) -> None:
        bom = get_active_bom(db, mid)
        if bom is None:
            return
        for line in bom.lines:
            if line.material_id == material_id or line.material_id in path:
                chain = " → ".join(str(m) for m in (*path, mid, line.material_id))
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"BOM 子件链路存在环路（{chain}），请检查物料 "
                           f"{material_id} 的 BOM 定义",
                )
            walk(line.material_id, (*path, mid))

    for cid in child_ids:
        walk(cid, (material_id,))


def _validate_lines(db: Session, material_id: int, payload: BomPut) -> None:
    """用量/损耗/物料存在性/环路校验，违规报中文 400。"""
    for line in payload.lines:
        if _d(line.qty_per) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"子件用量 qty_per 必须大于 0（物料 {line.material_id}）",
            )
        if not (Decimal("0") <= _d(line.loss_pct) <= Decimal("100")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"损耗率 loss_pct 必须在 0~100 之间（物料 {line.material_id}）",
            )
        if db.get(Material, line.material_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"子件物料不存在：{line.material_id}",
            )
    _check_cycle(db, material_id, payload.lines)


@router.get("/{material_id}", response_model=BomOut)
def get_bom(
    material_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Bom:
    bom = get_active_bom(db, material_id)
    if bom is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="该物料尚未维护 BOM"
        )
    return bom


@router.put("/{material_id}", response_model=BomOut, dependencies=[Depends(require_action("bom.write"))])
def put_bom(
    material_id: int,
    payload: BomPut,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Bom:
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"物料不存在：{material_id}"
        )
    _validate_lines(db, material_id, payload)

    bom = get_active_bom(db, material_id)
    action = "update" if bom is not None else "create"
    old_count = len(bom.lines) if bom is not None else 0
    if bom is None:
        bom = Bom(material_id=material_id, status="active")
        db.add(bom)
        db.flush()
    else:
        # 整体替换：旧行全部删除后写入新行
        for old in list(bom.lines):
            db.delete(old)
        db.flush()
    for line in payload.lines:
        db.add(
            BomLine(
                bom_id=bom.id,
                material_id=line.material_id,
                qty_per=_d(line.qty_per),
                loss_pct=_d(line.loss_pct),
            )
        )
    db.flush()
    write_audit(
        db, DOC_TYPE, bom.id, action, current_user.username,
        field="lines", old=f"{old_count} 行", new=f"{len(payload.lines)} 行",
    )
    write_op_log(
        db, current_user.username, f"bom_{action}",
        f"维护 BOM：物料 {material.code}，子件 {len(payload.lines)} 项",
    )
    db.commit()
    db.refresh(bom)
    return bom
