"""Add organization integration settings

Revision ID: f5c42c878153
Revises: 3a9510003910
Create Date: 2026-02-08 16:12:42.359715

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5c42c878153'
down_revision: Union[str, None] = '3a9510003910'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns only (SQLite doesn't support ALTER COLUMN)
    op.add_column('integrations', sa.Column('config', sa.JSON(), nullable=True))
    op.add_column('integrations', sa.Column('uses_org_credentials', sa.Boolean(), nullable=True))
    op.add_column('organizations', sa.Column('integration_settings', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'integration_settings')
    op.drop_column('integrations', 'uses_org_credentials')
    op.drop_column('integrations', 'config')
