"""批次内核：batches 表 + materials 批次开关/UDI + inventory_moves.batch_id（V2-S2）

Revision ID: 0004_batches_udi
Revises: 0003_users_role
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004_batches_udi'
down_revision: Union[str, Sequence[str], None] = '0003_users_role'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """批次台账表 + 物料批次开关与 UDI 字段 + 库存流水批次外键。"""
    op.create_table(
        'batches',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('batch_no', sa.String(64), nullable=False),
        sa.Column('material_id', sa.Integer(),
                  sa.ForeignKey('materials.id'), nullable=False),
        sa.Column('source_type', sa.String(32), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.Integer(),
                  sa.ForeignKey('partners.id'), nullable=True),
        sa.Column('insp_status', sa.String(16), nullable=False,
                  server_default='pending'),
        sa.Column('disposition', sa.String(16), nullable=True),
        sa.Column('disposition_remark', sa.String(255), nullable=True),
        sa.Column('created_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint('batch_no', name='uq_batches_batch_no'),
    )
    op.create_index('ix_batches_batch_no', 'batches', ['batch_no'])

    op.add_column(
        'materials',
        sa.Column('batch_required', sa.Boolean(), nullable=False,
                  server_default=sa.false()),
    )
    op.add_column('materials', sa.Column('udi_di', sa.String(64), nullable=True))
    op.add_column(
        'materials', sa.Column('udi_pi_rule', sa.String(128), nullable=True)
    )

    # batch_alter_table：SQLite 不支持 ALTER ADD/DROP CONSTRAINT，
    # batch 模式用 copy-and-move 实现建/删带 FK 的列；PostgreSQL 下退化为普通 ALTER
    with op.batch_alter_table('inventory_moves') as batch_op:
        batch_op.add_column(
            sa.Column(
                'batch_id', sa.Integer(),
                sa.ForeignKey('batches.id', name='fk_inventory_moves_batch_id'),
                nullable=True,
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table('inventory_moves') as batch_op:
        batch_op.drop_column('batch_id')
    op.drop_column('materials', 'udi_pi_rule')
    op.drop_column('materials', 'udi_di')
    op.drop_column('materials', 'batch_required')
    op.drop_index('ix_batches_batch_no', table_name='batches')
    op.drop_table('batches')
