"""v3 全链路闭环：新表（退货/盘点）+ 新列（审计/拒绝原因/完工量/损失/撤销/冲回/检验双段）

Revision ID: 0007_v3_closed_loops
Revises: 0006_partner_certs
Create Date: 2026-11-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007_v3_closed_loops'
down_revision: Union[str, Sequence[str], None] = '0006_partner_certs'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """v3 闭环改造：users.role 为 VARCHAR 扩枚举无需 DDL；其余加列/建表。

    检验记录双段制回填：inspector→recorder/judge_by，inspected_at→judged_at，
    conclusion→suggest_conclusion（存量记录视为「录入即判定」）。
    """
    # --- audit_records：+role/doc_no ---
    op.add_column('audit_records', sa.Column('role', sa.String(16), nullable=True))
    op.add_column('audit_records', sa.Column('doc_no', sa.String(32), nullable=True))
    # --- operation_logs：+doc_type/doc_id ---
    op.add_column('operation_logs', sa.Column('doc_type', sa.String(32), nullable=True))
    op.add_column('operation_logs', sa.Column('doc_id', sa.Integer(), nullable=True))

    # --- 单据拒绝原因 ---
    op.add_column('purchase_orders', sa.Column('reject_reason', sa.String(255), nullable=True))
    op.add_column('sales_orders', sa.Column('reject_reason', sa.String(255), nullable=True))
    op.add_column('manufacturing_orders', sa.Column('reject_reason', sa.String(255), nullable=True))

    # --- MO 完工量持久化 ---
    op.add_column('manufacturing_orders',
                  sa.Column('received_qty', sa.Numeric(18, 4), nullable=False, server_default='0'))
    op.add_column('manufacturing_orders',
                  sa.Column('completed_qty', sa.Numeric(18, 4), nullable=False, server_default='0'))

    # --- 批次损失金额 ---
    op.add_column('batches', sa.Column('loss_amount', sa.Numeric(18, 4), nullable=False,
                                       server_default='0'))

    # --- 资质撤销 + 录入人（不得自审依据）---
    op.add_column('partner_certs', sa.Column('created_by', sa.String(64), nullable=True))
    op.add_column('partner_certs', sa.Column('revoke_reason', sa.String(255), nullable=True))
    op.add_column('partner_certs', sa.Column('revoked_by', sa.String(64), nullable=True))
    op.add_column('partner_certs', sa.Column('revoked_at', sa.DateTime(), nullable=True))

    # --- 核销冲回引用 ---
    op.add_column('settlements', sa.Column('reversed_settlement_id', sa.Integer(), nullable=True))

    # --- 检验记录双段制 ---
    op.add_column('inspection_records', sa.Column('suggest_conclusion', sa.String(8), nullable=True))
    op.add_column('inspection_records', sa.Column('recorder', sa.String(64), nullable=True))
    op.add_column('inspection_records', sa.Column('judge_by', sa.String(64), nullable=True))
    op.add_column('inspection_records', sa.Column('judged_at', sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE inspection_records SET suggest_conclusion = conclusion, "
        "recorder = inspector, judge_by = inspector, judged_at = inspected_at "
        "WHERE conclusion IS NOT NULL"
    )
    op.alter_column('inspection_records', 'conclusion',
                    existing_type=sa.String(8), nullable=True)

    # --- 采购退货单 ---
    op.create_table(
        'purchase_returns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doc_no', sa.String(32), nullable=False),
        sa.Column('po_id', sa.Integer(), sa.ForeignKey('purchase_orders.id'), nullable=False),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id'), nullable=False),
        sa.Column('qty', sa.Numeric(18, 4), nullable=False),
        sa.Column('amount', sa.Numeric(18, 4), nullable=False),
        sa.Column('reason', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_purchase_returns_doc_no', 'purchase_returns', ['doc_no'], unique=True)

    # --- 销售退货单 ---
    op.create_table(
        'sales_returns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doc_no', sa.String(32), nullable=False),
        sa.Column('so_id', sa.Integer(), sa.ForeignKey('sales_orders.id'), nullable=False),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id'), nullable=False),
        sa.Column('qty', sa.Numeric(18, 4), nullable=False),
        sa.Column('amount', sa.Numeric(18, 4), nullable=False),
        sa.Column('reason', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_sales_returns_doc_no', 'sales_returns', ['doc_no'], unique=True)

    # --- 盘点单 ---
    op.create_table(
        'stock_counts',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('doc_no', sa.String(32), nullable=False),
        sa.Column('warehouse_id', sa.Integer(), sa.ForeignKey('warehouses.id'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='draft'),
        sa.Column('remark', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('posted_by', sa.String(64), nullable=True),
        sa.Column('posted_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_stock_counts_doc_no', 'stock_counts', ['doc_no'], unique=True)
    op.create_table(
        'stock_count_lines',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('count_id', sa.Integer(), sa.ForeignKey('stock_counts.id'), nullable=False),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id'), nullable=True),
        sa.Column('system_qty', sa.Numeric(18, 4), nullable=False),
        sa.Column('counted_qty', sa.Numeric(18, 4), nullable=True),
        sa.Column('diff_qty', sa.Numeric(18, 4), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_table('stock_count_lines')
    op.drop_table('stock_counts')
    op.drop_index('ix_sales_returns_doc_no', table_name='sales_returns')
    op.drop_table('sales_returns')
    op.drop_index('ix_purchase_returns_doc_no', table_name='purchase_returns')
    op.drop_table('purchase_returns')

    op.execute(
        "UPDATE inspection_records SET inspector = COALESCE(judge_by, recorder), "
        "conclusion = COALESCE(conclusion, suggest_conclusion) "
        "WHERE suggest_conclusion IS NOT NULL"
    )
    op.alter_column('inspection_records', 'conclusion',
                    existing_type=sa.String(8), nullable=False)
    op.drop_column('inspection_records', 'judged_at')
    op.drop_column('inspection_records', 'judge_by')
    op.drop_column('inspection_records', 'recorder')
    op.drop_column('inspection_records', 'suggest_conclusion')

    op.drop_column('settlements', 'reversed_settlement_id')
    op.drop_column('partner_certs', 'revoked_at')
    op.drop_column('partner_certs', 'revoked_by')
    op.drop_column('partner_certs', 'revoke_reason')
    op.drop_column('partner_certs', 'created_by')
    op.drop_column('batches', 'loss_amount')
    op.drop_column('manufacturing_orders', 'completed_qty')
    op.drop_column('manufacturing_orders', 'received_qty')
    op.drop_column('manufacturing_orders', 'reject_reason')
    op.drop_column('sales_orders', 'reject_reason')
    op.drop_column('purchase_orders', 'reject_reason')
    op.drop_column('operation_logs', 'doc_id')
    op.drop_column('operation_logs', 'doc_type')
    op.drop_column('audit_records', 'doc_no')
    op.drop_column('audit_records', 'role')
