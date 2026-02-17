"""Add domain fields to organization

Revision ID: 539cf0ef35cc
Revises: b9c8d7e6f5a4
Create Date: 2026-02-16 00:23:21.654169

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '539cf0ef35cc'
down_revision: Union[str, None] = 'b9c8d7e6f5a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add domain-based membership fields to organizations."""
    op.add_column('organizations', sa.Column('domain', sa.String(length=255), nullable=True))
    op.add_column('organizations', sa.Column('require_sso_for_domain', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('organizations', sa.Column('auto_join_domain', sa.Boolean(), server_default='true', nullable=False))
    op.create_index(op.f('ix_organizations_domain'), 'organizations', ['domain'], unique=False)


def downgrade() -> None:
    """Remove domain-based membership fields from organizations."""
    op.drop_index(op.f('ix_organizations_domain'), table_name='organizations')
    op.drop_column('organizations', 'auto_join_domain')
    op.drop_column('organizations', 'require_sso_for_domain')
    op.drop_column('organizations', 'domain')
