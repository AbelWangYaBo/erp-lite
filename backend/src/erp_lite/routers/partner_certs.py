"""供应商资质管理（V2-S4）：录入/审核/临期预警。

权限：partner_cert.write = purchase/admin（录入/编辑 draft），
partner_cert.audit = qa/admin（审核 → audited）；列表与临期查询全员可读。
"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_lite.database import get_db
from erp_lite.deps import ensure_not_self, get_current_user, require_action
from erp_lite.models import CertStatus, Partner, PartnerCert, PartnerType, User
from erp_lite.routers.common import paginate
from erp_lite.schemas.partner_certs import (
    CertRevokeIn,
    ExpiringCertOut,
    ExpiringCertPage,
    PartnerCertCreate,
    PartnerCertOut,
    PartnerCertPage,
    PartnerCertUpdate,
)
from erp_lite.services.audit import write_audit, write_op_log

router = APIRouter(
    prefix="/api/v1/partner-certs",
    tags=["partner-certs"],
    dependencies=[Depends(get_current_user)],
)

DOC_TYPE = "partner_cert"


def _get_or_404(db: Session, cert_id: int) -> PartnerCert:
    obj = db.get(PartnerCert, cert_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="供应商资质不存在")
    return obj


def _cert_out(cert: PartnerCert) -> dict:
    out = PartnerCertOut.model_validate(cert)
    out.partner_name = cert.partner.name
    return out.model_dump(mode="json")


@router.get("/expiring", response_model=ExpiringCertPage)
def list_expiring_certs(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """临期资质预警：audited 且 today ≤ valid_until ≤ today+N 天，按到期日升序。"""
    today = date.today()
    stmt = (
        select(PartnerCert)
        .where(
            PartnerCert.audit_status == CertStatus.audited,
            PartnerCert.valid_until >= today,
            PartnerCert.valid_until <= today + timedelta(days=days),
        )
        .order_by(PartnerCert.valid_until)
    )
    items, total = paginate(db, stmt, page=1, page_size=100)
    out = []
    for cert in items:
        data = _cert_out(cert)
        data["days_left"] = (cert.valid_until - today).days
        out.append(data)
    return {"items": out, "total": total}


@router.get("", response_model=PartnerCertPage)
def list_partner_certs(
    partner_id: int | None = None,
    audit_status: CertStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    stmt = select(PartnerCert).order_by(PartnerCert.id)
    if partner_id is not None:
        stmt = stmt.where(PartnerCert.partner_id == partner_id)
    if audit_status is not None:
        stmt = stmt.where(PartnerCert.audit_status == audit_status)
    items, total = paginate(db, stmt, page, page_size)
    return {"items": [_cert_out(c) for c in items], "total": total}


@router.get("/{cert_id}", response_model=PartnerCertOut)
def get_partner_cert(
    cert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    return _cert_out(_get_or_404(db, cert_id))


@router.post(
    "",
    response_model=PartnerCertOut,
    dependencies=[Depends(require_action("partner_cert.write"))],
)
def create_partner_cert(
    payload: PartnerCertCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """录入资质（draft）：仅 supplier 可挂，审核后才对采购生效。"""
    partner = db.get(Partner, payload.partner_id)
    if partner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="往来单位不存在")
    if partner.type != PartnerType.supplier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅供应商（type=supplier）可挂资质证照",
        )
    if payload.valid_until < payload.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="有效期至早于生效日期，请检查证照有效期",
        )
    cert = PartnerCert(**payload.model_dump())
    cert.created_by = current_user.username
    db.add(cert)
    db.flush()
    write_audit(db, DOC_TYPE, cert.id, "create", current_user.username,
                role=current_user.role, doc_no=cert.cert_no)
    write_op_log(
        db, current_user.username, "partner_cert_create",
        f"录入供应商资质：{partner.name} {cert.cert_name}（{cert.cert_no}）",
        doc_type=DOC_TYPE, doc_id=cert.id,
    )
    db.commit()
    db.refresh(cert)
    return _cert_out(cert)


@router.put(
    "/{cert_id}",
    response_model=PartnerCertOut,
    dependencies=[Depends(require_action("partner_cert.write"))],
)
def update_partner_cert(
    cert_id: int,
    payload: PartnerCertUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """编辑资质：仅 draft 可改；逐字段 diff 留痕。"""
    cert = _get_or_404(db, cert_id)
    if cert.audit_status != CertStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅草稿状态的资质可修改，已审核资质请重新录入",
        )
    for field, new in payload.model_dump(exclude_unset=True).items():
        if new is None:
            continue
        old = getattr(cert, field)
        if str(old) != str(new):
            write_audit(
                db, DOC_TYPE, cert.id, "update", current_user.username,
                field=field, old=None if old is None else str(old), new=str(new),
            )
            setattr(cert, field, new)
    write_op_log(
        db, current_user.username, "partner_cert_update",
        f"修改供应商资质：{cert.partner.name} {cert.cert_name}（draft）",
    )
    db.commit()
    db.refresh(cert)
    return _cert_out(cert)


@router.post(
    "/{cert_id}/audit",
    response_model=PartnerCertOut,
    dependencies=[Depends(require_action("partner_cert.audit"))],
)
def audit_partner_cert(
    cert_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """资质审核：draft → audited，记录 audited_by / audited_at（GMP 留痕）。"""
    cert = _get_or_404(db, cert_id)
    if cert.audit_status != CertStatus.draft:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅草稿状态的资质可审核，不可重复审核",
        )
    # 规则 A｜不得自审：资质录入人不得自行审核
    ensure_not_self(current_user, cert.created_by, "资质审核")
    old_status = cert.audit_status.value
    cert.audit_status = CertStatus.audited
    cert.audited_by = current_user.username
    cert.audited_at = datetime.now()
    write_audit(
        db, DOC_TYPE, cert.id, "audit", current_user.username,
        role=current_user.role, doc_no=cert.cert_no,
        field="audit_status", old=old_status, new=CertStatus.audited.value,
    )
    write_op_log(
        db, current_user.username, "partner_cert_audit",
        f"审核供应商资质：{cert.partner.name} {cert.cert_name}（{cert.cert_no}）→ audited",
        doc_type=DOC_TYPE, doc_id=cert.id,
    )
    db.commit()
    db.refresh(cert)
    return _cert_out(cert)


@router.post(
    "/{cert_id}/revoke",
    response_model=PartnerCertOut,
    dependencies=[Depends(require_action("partner_cert.revoke"))],
)
def revoke_partner_cert(
    cert_id: int,
    body: CertRevokeIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """资质撤销（v3）：audited → revoked（吊销/失效立即可拦，不再等自然过期）。

    撤销后证照不参与 PO 资质拦截判定；原因/人/时留痕。
    """
    cert = _get_or_404(db, cert_id)
    if cert.audit_status != CertStatus.audited:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅已审核（audited）状态的资质可撤销",
        )
    # 规则 A｜不得自审：资质审核人不得自行撤销
    ensure_not_self(current_user, cert.audited_by, "资质撤销")
    old_status = cert.audit_status.value
    cert.audit_status = CertStatus.revoked
    cert.revoke_reason = body.reason
    cert.revoked_by = current_user.username
    cert.revoked_at = datetime.now()
    write_audit(
        db, DOC_TYPE, cert.id, "revoke", current_user.username,
        role=current_user.role, doc_no=cert.cert_no,
        field="audit_status", old=old_status, new=CertStatus.revoked.value,
    )
    write_op_log(
        db, current_user.username, "partner_cert_revoke",
        f"撤销供应商资质：{cert.partner.name} {cert.cert_name}（{cert.cert_no}）→ revoked，"
        f"原因：{body.reason}",
        doc_type=DOC_TYPE, doc_id=cert.id,
    )
    db.commit()
    db.refresh(cert)
    return _cert_out(cert)
