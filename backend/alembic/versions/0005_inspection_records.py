"""检验记录表（V2-S3）

Revision ID: 0005_inspection_records
Revises: 0004_batches_udi
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005_inspection_records'
down_revision: Union[str, Sequence[str], None] = '0004_batches_udi'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """检验记录表：一次 QA 判定一条（检项 JSON / 结论 / 检验人 / 时间 / 备注）。"""
    op.create_table(
        'inspection_records',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('batch_id', sa.Integer(),
                  sa.ForeignKey('batches.id'), nullable=False),
        sa.Column('items_json', sa.JSON(), nullable=False),
        sa.Column('conclusion', sa.String(8), nullable=False),
        sa.Column('inspector', sa.String(64), nullable=True),
        sa.Column('inspected_at', sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.Column('remark', sa.String(255), nullable=True),
    )
    op.create_index(
        'ix_inspection_records_batch_id', 'inspection_records', ['batch_id']
    )


def downgrade() -> None:
    op.drop_index('ix_inspection_records_batch_id', table_name='inspection_records')
    op.drop_table('inspection_records')
