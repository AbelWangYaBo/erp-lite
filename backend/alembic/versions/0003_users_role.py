"""users.role 列（v2 RBAC 基座）

Revision ID: 0003_users_role
Revises: 0002_mo_transferred_cost
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003_users_role'
down_revision: Union[str, Sequence[str], None] = '0002_mo_transferred_cost'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """users 增加 role 枚举列，存量用户统一回填 admin。"""
    op.add_column(
        'users',
        sa.Column('role', sa.String(16), nullable=False, server_default='admin'),
    )


def downgrade() -> None:
    op.drop_column('users', 'role')
