"""采购/销售订单公共逻辑：引用校验、草稿创建、草稿逐字段修改留痕。"""

import json

from sqlalchemy.orm import Session

from erp_lite.models import Material, Partner, User, Warehouse
from erp_lite.schemas.orders import OrderLineCreate
from erp_lite.services.audit import write_audit


def _bad(detail: str):
    from fastapi import HTTPException, status

    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def validate_order_refs(db: Session, partner_id: int, lines: list[OrderLineCreate]) -> None:
    """校验 partner / material / warehouse 引用存在（SQLite 不强制外键，业务层兜底）。"""
    if db.get(Partner, partner_id) is None:
        raise _bad("往来单位不存在")
    for line in lines:
        if db.get(Material, line.material_id) is None:
            raise _bad(f"物料不存在：{line.material_id}")
        if db.get(Warehouse, line.warehouse_id) is None:
            raise _bad(f"仓库不存在：{line.warehouse_id}")


def format_lines(lines) -> str:
    """行明细压缩为审计可读字符串（截断到 255）。"""
    data = [
        {
            "material_id": l.material_id,
            "qty": str(l.qty),
            "unit_price": str(l.unit_price),
            "warehouse_id": l.warehouse_id,
        }
        for l in lines
    ]
    return json.dumps(data, ensure_ascii=False)[:255]


def create_order(db: Session, *, order_model, line_model, payload, created_by: str):
    """建草稿单：由调用方负责 doc_no / 审计 / 提交。"""
    order = order_model(
        partner_id=payload.partner_id,
        order_date=payload.order_date,
        remark=payload.remark,
        created_by=created_by,
        lines=[
            line_model(
                material_id=l.material_id,
                qty=l.qty,
                unit_price=l.unit_price,
                warehouse_id=l.warehouse_id,
            )
            for l in payload.lines
        ],
    )
    if hasattr(payload, "expected_date"):
        order.expected_date = payload.expected_date
    db.add(order)
    # 不在此处 flush：调用方需先赋 doc_no（非空列），再统一 flush 落库
    return order


def update_draft_order(
    db: Session,
    order,
    payload,
    line_model,
    doc_type: str,
    actor: User,
) -> int:
    """草稿单逐字段修改：标量字段 diff 留痕，行明细整体替换留痕。返回变更字段数。"""
    changed = 0
    for field in ("partner_id", "order_date", "expected_date", "remark"):
        new = getattr(payload, field, None)
        if new is None:
            continue
        old = getattr(order, field)
        if str(old) != str(new):
            write_audit(
                db,
                doc_type=doc_type,
                doc_id=order.id,
                action="update",
                actor=actor.username,
                field=field,
                old=None if old is None else str(old),
                new=str(new),
            )
            setattr(order, field, new)
            changed += 1
    if payload.lines is not None:
        old_fmt = format_lines(order.lines)
        order.lines = [
            line_model(
                material_id=l.material_id,
                qty=l.qty,
                unit_price=l.unit_price,
                warehouse_id=l.warehouse_id,
            )
            for l in payload.lines
        ]
        write_audit(
            db,
            doc_type=doc_type,
            doc_id=order.id,
            action="update",
            actor=actor.username,
            field="lines",
            old=old_fmt,
            new=format_lines(order.lines),
        )
        changed += 1
    return changed
