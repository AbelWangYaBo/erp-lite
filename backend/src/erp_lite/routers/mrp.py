"""MRP 运算：跑运算 / 建议查询 / 建议一键转 PO、MO 草稿（幂等）。"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import (
    ManufacturingOrder,
    Material,
    MrpRun,
    MrpSuggestion,
    Partner,
    PartnerType,
    PurchaseOrder,
    PurchaseOrderLine,
    User,
)
from erp_lite.models.base import DocStatus
from erp_lite.routers.common import paginate
from erp_lite.schemas.production import (
    MrpConvertIn,
    MrpRunIn,
    MrpSuggestionOut,
    MrpSuggestionPage,
)
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.certs import ensure_supplier_certified
from erp_lite.services.inventory import _d
from erp_lite.services.mrp import run_mrp
from erp_lite.services.numbering import next_doc_no

router = APIRouter(
    prefix="/api/v1/mrp",
    tags=["mrp"],
    dependencies=[Depends(get_current_user)],
)


def _get_suggestion_or_400(db: Session, sug_id: int) -> MrpSuggestion:
    sug = db.get(MrpSuggestion, sug_id)
    if sug is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"MRP 建议不存在：{sug_id}",
        )
    return sug


def _default_warehouse_or_400(material: Material) -> int:
    if material.default_warehouse_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"物料 {material.code}（{material.name}）未设置默认仓库，无法转单",
        )
    return material.default_warehouse_id


@router.post("/run", dependencies=[Depends(require_action("mrp.run"))])
def run_mrp_api(
    payload: MrpRunIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    try:
        suggestions = run_mrp(
            db, [(d.material_id, _d(d.qty)) for d in payload.demand]
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    run = MrpRun(
        run_date=date.today(),
        demand_type="manual",
        params_json={"demand": [d.model_dump(mode="json") for d in payload.demand]},
        result_json={
            "suggestions": [
                {**s, "net_qty": str(s["net_qty"])} for s in suggestions
            ]
        },
    )
    db.add(run)
    db.flush()
    for sug in suggestions:
        db.add(
            MrpSuggestion(
                run_id=run.id,
                material_id=sug["material_id"],
                type=sug["type"],
                net_qty=sug["net_qty"],
            )
        )
    db.flush()
    write_op_log(
        db, current_user.username, "mrp_run",
        f"MRP 运算：需求 {len(payload.demand)} 项，产出建议 "
        f"{len(suggestions)} 条（run #{run.id}）",
    )
    db.commit()
    rows = db.scalars(
        select(MrpSuggestion).where(MrpSuggestion.run_id == run.id).order_by(MrpSuggestion.id)
    ).all()
    return {
        "run_id": run.id,
        "suggestions": [
            MrpSuggestionOut.model_validate(r).model_dump(mode="json") for r in rows
        ],
    }


@router.get("/suggestions", response_model=MrpSuggestionPage)
def list_suggestions(
    converted: bool | None = Query(default=None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(MrpSuggestion).order_by(MrpSuggestion.id)
    if converted is not None:
        stmt = stmt.where(MrpSuggestion.converted == converted)
    items, total = paginate(db, stmt, page, page_size)
    return {
        "items": [
            MrpSuggestionOut.model_validate(s).model_dump(mode="json") for s in items
        ],
        "total": total,
    }


@router.post("/suggestions/convert", dependencies=[Depends(require_action("mrp.convert"))])
def convert_suggestions(
    payload: MrpConvertIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    sugs = [_get_suggestion_or_400(db, i) for i in payload.ids]
    for sug in sugs:
        if sug.converted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"MRP 建议 #{sug.id} 已转换，禁止重复转换",
            )
    buy_sugs = [s for s in sugs if s.type == "buy"]
    make_sugs = [s for s in sugs if s.type == "make"]
    if buy_sugs and payload.partner_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="转换采购建议需提供供应商（partner_id）",
        )
    supplier = None
    if buy_sugs:
        supplier = db.get(Partner, payload.partner_id)
        if supplier is None or supplier.type != PartnerType.supplier:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"供应商不存在或类型错误：{payload.partner_id}",
            )
        # v3：堵资质拦截旁路——MRP 转 PO 与手工建单同一资质校验
        ensure_supplier_certified(db, supplier.id, current_user.username)

    today = date.today()
    converted: list[dict] = []

    if buy_sugs:
        po = PurchaseOrder(
            doc_no=next_doc_no(db, "PO", today),
            status=DocStatus.draft,
            partner_id=supplier.id,
            order_date=today,
            created_by=current_user.username,
            remark="MRP 建议转采购",
        )
        db.add(po)
        db.flush()
        for sug in buy_sugs:
            material = db.get(Material, sug.material_id)
            wh_id = _default_warehouse_or_400(material)
            db.add(
                PurchaseOrderLine(
                    po_id=po.id,
                    material_id=sug.material_id,
                    qty=sug.net_qty,
                    unit_price=_d(0),  # 0 价草稿：审核/入库前应改价（0 价入库会生成 0 应付）
                    warehouse_id=wh_id,
                )
            )
            sug.converted = True
            sug.converted_doc_id = po.id
            converted.append({"suggestion_id": sug.id, "doc_type": "purchase_order", "doc_id": po.id})
        write_audit(db, "purchase_order", po.id, "create", current_user.username,
                    role=current_user.role, doc_no=po.doc_no)
        write_op_log(
            db, current_user.username, "mrp_convert_warning",
            f"MRP 转采购 {po.doc_no} 行单价为 0，请在审核前修改行单价",
            doc_type="purchase_order", doc_id=po.id,
        )
        db.flush()

    for sug in make_sugs:
        material = db.get(Material, sug.material_id)
        wh_id = _default_warehouse_or_400(material)
        mo = ManufacturingOrder(
            doc_no=next_doc_no(db, "MO", today),
            material_id=sug.material_id,
            planned_qty=sug.net_qty,
            warehouse_id=wh_id,
            created_by=current_user.username,
            remark="MRP 建议转生产",
        )
        db.add(mo)
        db.flush()
        sug.converted = True
        sug.converted_doc_id = mo.id
        write_audit(db, "manufacturing_order", mo.id, "create", current_user.username,
                    role=current_user.role, doc_no=mo.doc_no)
        converted.append({"suggestion_id": sug.id, "doc_type": "manufacturing_order", "doc_id": mo.id})

    write_op_log(
        db, current_user.username, "mrp_convert",
        f"MRP 建议转单：{len(converted)} 条（采购 {len(buy_sugs)}、生产 {len(make_sugs)}）",
    )
    db.commit()
    return {"converted": converted}
