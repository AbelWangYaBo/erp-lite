"""mo transferred_cost

Revision ID: 0002_mo_transferred_cost
Revises: 0001_initial
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002_mo_transferred_cost'
down_revision: Union[str, Sequence[str], None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """MO 增加已结转成本字段（存量工单按 0 初始化）。"""
    op.add_column(
        'manufacturing_orders',
        sa.Column('transferred_cost', sa.Numeric(18, 4), nullable=False,
                  server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('manufacturing_orders', 'transferred_cost')
