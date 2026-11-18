"""财务 API：应收应付列表、收付款核销、期间对账单。

核销规则：
- payment（付款）只能核销应付（ap），receipt（收款）只能核销应收（ar），错配 400；
- 单据必须属于该往来单位，跨 partner 400；
- 单笔金额 > 0（schema 校验），累计核销不得超过 amount - settled_amount，超额 400；
- 核销写 Settlement/SettlementLine 并更新 AP/AR 的 settled_amount 与 status
  （全额→settled，部分→partial），全部同一事务提交 + 审计留痕 + 操作日志。
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import get_current_user, require_action
from erp_lite.models import (
    Payable,
    Partner,
    PartnerType,
    PurchaseOrder,
    Receivable,
    SalesOrder,
    Settlement,
    SettlementLine,
    User,
)
from erp_lite.models.base import SettleStatus
from erp_lite.routers.common import paginate
from erp_lite.schemas.finance import (
    PayablePage,
    ReceivablePage,
    SettlementCreate,
    SettlementOut,
    SettlementPage,
    SettlementReverseIn,
    StatementOut,
)
from erp_lite.services.audit import write_audit, write_op_log
from erp_lite.services.inventory import q4

router = APIRouter(
    prefix="/api/v1", tags=["finance"], dependencies=[Depends(get_current_user)]
)

_TARGET_MODEL = {"ap": Payable, "ar": Receivable}
_TYPE_FOR_TARGET = {"ap": "payment", "ar": "receipt"}


def _list_targets(
    db: Session,
    model: type[Payable] | type[Receivable],
    partner_id: int | None,
    settle_status: SettleStatus | None,
    page: int,
    page_size: int,
) -> dict:
    stmt = select(model).order_by(model.id.desc())
    if partner_id is not None:
        stmt = stmt.where(model.partner_id == partner_id)
    if settle_status is not None:
        stmt = stmt.where(model.status == settle_status)
    rows, total = paginate(db, stmt, page, page_size)
    partner_names = {
        p.id: p.name
        for p in db.scalars(
            select(Partner).where(Partner.id.in_({r.partner_id for r in rows} or {None}))
        ).all()
    }
    # v3：来源单号一并返回（前端 DocLink 可点回 PO/SO）
    source_nos = _source_doc_nos(
        db, [(r.source_type, r.source_id) for r in rows]
    )
    items = []
    for r in rows:
        item = {
            "id": r.id,
            "partner_id": r.partner_id,
            "partner_name": partner_names.get(r.partner_id, ""),
            "source_type": r.source_type,
            "source_id": r.source_id,
            "source_no": source_nos.get((r.source_type, r.source_id), ""),
            "amount": r.amount,
            "settled_amount": r.settled_amount,
            "status": r.status,
            "created_at": r.created_at,
        }
        items.append(item)
    return {"items": items, "total": total}


@router.get("/payables", response_model=PayablePage, dependencies=[Depends(require_action("ap.read"))])
def list_payables(
    partner_id: int | None = None,
    settle_status: SettleStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return _list_targets(db, Payable, partner_id, settle_status, page, page_size)


@router.get("/receivables", response_model=ReceivablePage, dependencies=[Depends(require_action("ar.read"))])
def list_receivables(
    partner_id: int | None = None,
    settle_status: SettleStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return _list_targets(db, Receivable, partner_id, settle_status, page, page_size)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_200_OK, dependencies=[Depends(require_action("settlement.create"))])
def create_settlement(
    body: SettlementCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    expected_target = "ap" if body.type == "payment" else "ar"
    if any(line.target_type != expected_target for line in body.lines):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "核销类型不匹配：payment 核销应付(ap)，receipt 核销应收(ar)"
            ),
        )

    partner = db.get(Partner, body.partner_id)
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="往来单位不存在"
        )

    model = _TARGET_MODEL[expected_target]
    targets: dict[int, Payable | Receivable] = {}
    for line in body.lines:
        if line.target_id in targets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="同一单据在一笔核销中重复出现，请合并金额",
            )
        # PG 下 FOR UPDATE 锁目标 AP/AR 行后再校验剩余额并累加 settled_amount，
        # 串行化同一单据的并发核销（防超额核销）；SQLite 忽略 FOR UPDATE。
        target = db.scalar(
            select(model).where(model.id == line.target_id).with_for_update()
        )
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"待核销单据不存在：id={line.target_id}",
            )
        if target.partner_id != body.partner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"单据 id={line.target_id} 不属于该往来单位，禁止核销",
            )
        remaining = q4(target.amount - target.settled_amount)
        if q4(line.amount) > remaining:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"核销金额 {line.amount} 超过单据 id={line.target_id} "
                    f"剩余待核销额 {remaining}"
                ),
            )
        targets[line.target_id] = target

    total = q4(sum(line.amount for line in body.lines))
    settlement = Settlement(
        type=body.type,
        partner_id=body.partner_id,
        amount=total,
        date=body.date,
        remark=body.remark,
        created_by=current_user.username,
    )
    db.add(settlement)
    db.flush()  # 取 settlement.id 供 lines 外键

    for line in body.lines:
        db.add(
            SettlementLine(
                settlement_id=settlement.id,
                target_type=line.target_type,
                target_id=line.target_id,
                amount=q4(line.amount),
            )
        )
        target = targets[line.target_id]
        old_settled = q4(target.settled_amount)
        new_settled = q4(old_settled + line.amount)
        target.settled_amount = new_settled
        target.status = (
            SettleStatus.settled if new_settled >= target.amount else SettleStatus.partial
        )
        write_audit(
            db,
            doc_type="payable" if expected_target == "ap" else "receivable",
            doc_id=target.id,
            action="settle",
            actor=current_user.username,
            role=current_user.role,
            doc_no=_target_doc_no(db, target),
            field="settled_amount",
            old=str(old_settled),
            new=str(new_settled),
        )

    write_op_log(
        db,
        current_user.username,
        "settlement_create",
        f"核销单 {body.type} partner={body.partner_id} 金额 {total} "
        f"明细 {len(body.lines)} 条",
        doc_type="settlement", doc_id=settlement.id,
    )
    db.commit()
    db.refresh(settlement)
    return _settlement_item(db, settlement)


@router.post(
    "/settlements/{settlement_id}/reverse",
    response_model=SettlementOut,
    dependencies=[Depends(require_action("settlement.reverse"))],
)
def reverse_settlement(
    settlement_id: int,
    body: SettlementReverseIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """核销冲回（v3）：生成冲回单恢复各单据未清额，原记录不删（审计链完整）。

    校验：原单未被冲回过；各 bill 当前已核销额足以回减（后续若有其他核销
    导致回减超额则 400）。回减后状态 settled→partial→open 重算。
    """
    src = db.get(Settlement, settlement_id)
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="核销单不存在")
    already = db.scalar(
        select(Settlement).where(Settlement.reversed_settlement_id == src.id)
    )
    if already is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"核销单 #{src.id} 已被冲回（冲回单 #{already.id}），禁止重复冲回",
        )
    model = _TARGET_MODEL["ap" if src.type == "payment" else "ar"]
    lines = sorted(src.lines, key=lambda l: l.id)
    # 预校验全部行可回减（任一行不足即失败，不产生半冲回）
    for line in lines:
        target = db.get(model, line.target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"原核销目标单据不存在：id={line.target_id}",
            )
        if q4(target.settled_amount) < q4(line.amount):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"单据 id={line.target_id} 当前已核销额 "
                    f"{q4(target.settled_amount)} 不足以冲回 {q4(line.amount)}"
                ),
            )
    reverse = Settlement(
        type=src.type,
        partner_id=src.partner_id,
        amount=src.amount,
        date=body.date,
        remark=body.remark or f"冲回核销单 #{src.id}",
        reversed_settlement_id=src.id,
        created_by=current_user.username,
    )
    db.add(reverse)
    db.flush()
    for line in lines:
        db.add(
            SettlementLine(
                settlement_id=reverse.id,
                target_type=line.target_type,
                target_id=line.target_id,
                amount=q4(line.amount),
            )
        )
        target = db.get(model, line.target_id)
        old_settled = q4(target.settled_amount)
        new_settled = q4(old_settled - q4(line.amount))
        target.settled_amount = new_settled
        target.status = (
            SettleStatus.settled
            if new_settled >= target.amount
            else (SettleStatus.open if new_settled <= 0 else SettleStatus.partial)
        )
        write_audit(
            db,
            doc_type="payable" if src.type == "payment" else "receivable",
            doc_id=target.id,
            action="reverse",
            actor=current_user.username,
            role=current_user.role,
            doc_no=_target_doc_no(db, target),
            field="settled_amount",
            old=str(old_settled),
            new=str(new_settled),
        )
    write_op_log(
        db,
        current_user.username,
        "settlement_reverse",
        f"冲回核销单 #{src.id}：生成冲回单 #{reverse.id}，金额 {src.amount}",
        doc_type="settlement", doc_id=reverse.id,
    )
    db.commit()
    db.refresh(reverse)
    return _settlement_item(db, reverse)


def _settlement_item(db: Session, s: Settlement) -> dict:
    """核销单出参：附加 partner 名称与 lines（含冲回引用）。"""
    partner = db.get(Partner, s.partner_id)
    return {
        "id": s.id,
        "type": s.type,
        "partner_id": s.partner_id,
        "partner_name": partner.name if partner else "",
        "amount": s.amount,
        "date": s.date,
        "remark": s.remark,
        "reversed_settlement_id": s.reversed_settlement_id,
        "created_by": s.created_by,
        "created_at": s.created_at,
        "lines": list(s.lines),
    }


def _target_doc_no(db: Session, target: Payable | Receivable) -> str | None:
    """AP/AR 审计的 doc_no：取来源单据号（对账串联用）。"""
    if target.source_type == "purchase_order":
        po = db.get(PurchaseOrder, target.source_id)
        return po.doc_no if po else None
    if target.source_type == "sales_order":
        so = db.get(SalesOrder, target.source_id)
        return so.doc_no if so else None
    return None


@router.get("/settlements", response_model=SettlementPage)
def list_settlements(
    partner_id: int | None = None,
    settle_type: str | None = Query(default=None, alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(Settlement).order_by(Settlement.id.desc())
    if partner_id is not None:
        stmt = stmt.where(Settlement.partner_id == partner_id)
    if settle_type is not None:
        stmt = stmt.where(Settlement.type == settle_type)
    rows, total = paginate(db, stmt, page, page_size)
    items = [_settlement_item(db, s) for s in rows]
    return {"items": items, "total": total}


@router.get("/finance/statement", response_model=StatementOut,
            dependencies=[Depends(require_action("statement.read"))])
def finance_statement(
    partner_id: int,
    type_: str = Query(..., alias="type"),
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """期间对账单（v3 勾稽口径）：

        期末未清 = 期初未清 + 期间新增 − 期间核销（冲回计负核销）

    期末未清 = Σ 全部账单 (amount − settled_amount)（现状事实，口径终点）；
    期间新增/期间核销按日期落入期间；期初由恒等式倒推，保证勾稽恒成立
    （修复 v2「期初被期间核销追溯污染、期初+发生−收付≠期末」断点）。
    """
    if type_ not in ("supplier", "customer"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="type 仅支持 supplier（应付对账）或 customer（应收对账）",
        )
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="往来单位不存在"
        )
    expected_type = (
        PartnerType.supplier if type_ == "supplier" else PartnerType.customer
    )
    if partner.type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"该往来单位类型为 {partner.type.value}，与对账类型 {type_} 不符",
        )

    target_type = "ap" if type_ == "supplier" else "ar"
    model = _TARGET_MODEL[target_type]
    settle_type = _TYPE_FOR_TARGET[target_type]

    # 全部账单（不限期间）→ 期末未清
    all_docs = db.scalars(
        select(model).where(model.partner_id == partner_id).order_by(model.created_at, model.id)
    ).all()
    closing_balance = q4(sum(q4(d.amount - d.settled_amount) for d in all_docs))

    # 期间新增账单
    docs_stmt = select(model).where(model.partner_id == partner_id)
    if from_ is not None:
        docs_stmt = docs_stmt.where(func.date(model.created_at) >= from_)
    if to is not None:
        docs_stmt = docs_stmt.where(func.date(model.created_at) <= to)
    docs_stmt = docs_stmt.order_by(model.created_at, model.id)
    docs = db.scalars(docs_stmt).all()
    period_new = q4(sum(q4(d.amount) for d in docs))

    source_nos = _source_doc_nos(db, [(d.source_type, d.source_id) for d in docs])
    details = []
    total_amount = total_settled = total_balance = q4(0)
    for d in docs:
        amount = q4(d.amount)
        settled = q4(d.settled_amount)
        balance = q4(amount - settled)
        total_amount += amount
        total_settled += settled
        total_balance += balance
        details.append(
            {
                "target_type": target_type,
                "target_id": d.id,
                "source_type": d.source_type,
                "source_id": d.source_id,
                "source_no": source_nos.get((d.source_type, d.source_id), ""),
                "date": d.created_at.date(),
                "amount": amount,
                "settled_amount": settled,
                "balance": balance,
            }
        )

    # 期间核销（含冲回单：冲回作为负核销参与勾稽）
    s_stmt = select(Settlement).where(
        Settlement.partner_id == partner_id, Settlement.type == settle_type
    )
    if from_ is not None:
        s_stmt = s_stmt.where(Settlement.date >= from_)
    if to is not None:
        s_stmt = s_stmt.where(Settlement.date <= to)
    s_stmt = s_stmt.order_by(Settlement.date, Settlement.id)
    settlements = db.scalars(s_stmt).all()
    settle_net = q4(0)
    settle_rows = []
    for s in settlements:
        signed = -q4(s.amount) if s.reversed_settlement_id is not None else q4(s.amount)
        settle_net = q4(settle_net + signed)
        settle_rows.append(
            {
                "id": s.id,
                "type": s.type,
                "date": s.date,
                "amount": q4(s.amount),
                "is_reverse": s.reversed_settlement_id is not None,
                "remark": s.remark,
            }
        )

    # 期初未清 = 期末 − 期间新增 + 期间核销（倒推，恒等式成立）
    opening_balance = q4(closing_balance - period_new + settle_net) if from_ is not None else q4(0)

    return {
        "partner_id": partner_id,
        "partner_name": partner.name,
        "partner_type": partner.type.value,
        "target_type": target_type,
        "from": from_,
        "to": to,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "details": details,
        "settlements": settle_rows,
        "totals": {
            "total_amount": total_amount,
            "total_settled": total_settled,
            "total_balance": total_balance,
            "period_new_amount": period_new,
            "settlement_total": settle_net,
        },
    }


def _source_doc_nos(db: Session, refs) -> dict[tuple[str, int], str]:
    """按 (source_type, source_id) 元组批量取来源单号（采购/销售订单）。"""
    po_ids = {sid for stype, sid in refs if stype == "purchase_order"}
    so_ids = {sid for stype, sid in refs if stype == "sales_order"}
    nos: dict[tuple[str, int], str] = {}
    if po_ids:
        for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_(po_ids))):
            nos[("purchase_order", po.id)] = po.doc_no
    if so_ids:
        for so in db.scalars(select(SalesOrder).where(SalesOrder.id.in_(so_ids))):
            nos[("sales_order", so.id)] = so.doc_no
    return nos
