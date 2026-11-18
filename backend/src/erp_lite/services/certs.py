"""供应商资质拦截（V2-S4 GMP 采购控制）。

PO create / approve 时校验供应商存在至少一张 audited 且 valid_until ≥ today 的证照；
非 supplier 往来单位不校验。拦截事件写 operation_logs（含原因），供追溯。
"""

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.models import CertStatus, Partner, PartnerCert, PartnerType
from erp_lite.services.audit import write_op_log

DOC_TYPE_BLOCK_ACTION = "partner_cert_block"


def _block(db: Session, partner: Partner, reason: str, actor: str | None) -> HTTPException:
    """拦截留痕（独立提交，业务事务随异常回滚也不丢拦截记录）后抛 400。"""
    write_op_log(
        db,
        actor or "system",
        DOC_TYPE_BLOCK_ACTION,
        f"采购拦截：供应商 {partner.name}（{partner.code}）{reason}",
    )
    db.commit()
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reason)


def ensure_supplier_certified(db: Session, partner_id: int, actor: str | None = None) -> None:
    """供应商资质有效性校验：无 audited 且未过期证照则 400（区分无有效资质/已过期）。

    - partner 不存在：静默返回（引用存在性由订单校验负责，提示「往来单位不存在」）
    - partner 非 supplier：不校验（客户往来不需要证照）
    """
    partner = db.get(Partner, partner_id)
    if partner is None or partner.type != PartnerType.supplier:
        return

    certs = db.scalars(
        select(PartnerCert).where(PartnerCert.partner_id == partner_id)
    ).all()
    today = date.today()
    # v3：revoked（撤销）证照不再参与有效性判定
    audited = [c for c in certs if c.audit_status == CertStatus.audited]

    if any(c.valid_until >= today for c in audited):
        return  # 至少一张 audited 且未过期证照，放行

    if not certs or not audited:
        # 一张证照都没有，或只有草稿（未审核不生效）
        if certs:
            reason = "供应商无有效资质（证照尚未审核），禁止下单，请先完成资质审核"
        else:
            reason = "供应商无有效资质，禁止下单，请先录入并审核资质证照"
        raise _block(db, partner, reason, actor)

    latest = max(c.valid_until for c in audited)
    raise _block(
        db,
        partner,
        f"供应商资质已过期（最晚有效至 {latest.isoformat()}），禁止下单，请更新资质",
        actor,
    )
