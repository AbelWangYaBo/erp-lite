"""供应商资质表（V2-S4）

Revision ID: 0006_partner_certs
Revises: 0005_inspection_records
Create Date: 2026-11-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006_partner_certs'
down_revision: Union[str, Sequence[str], None] = '0005_inspection_records'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """供应商资质证照：仅 supplier 可挂；audit_status draft/audited，审核后用于 PO 拦截。"""
    op.create_table(
        'partner_certs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('partner_id', sa.Integer(),
                  sa.ForeignKey('partners.id'), nullable=False),
        sa.Column('cert_name', sa.String(128), nullable=False),
        sa.Column('cert_no', sa.String(64), nullable=False),
        sa.Column('issuer', sa.String(128), nullable=True),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_until', sa.Date(), nullable=False),
        sa.Column('file_ref', sa.String(255), nullable=True),
        sa.Column('audit_status', sa.String(16), nullable=False,
                  server_default='draft'),
        sa.Column('audited_by', sa.String(64), nullable=True),
        sa.Column('audited_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_partner_certs_partner_id', 'partner_certs', ['partner_id'])
    op.create_index('ix_partner_certs_cert_no', 'partner_certs', ['cert_no'])


def downgrade() -> None:
    op.drop_index('ix_partner_certs_cert_no', table_name='partner_certs')
    op.drop_index('ix_partner_certs_partner_id', table_name='partner_certs')
    op.drop_table('partner_certs')
