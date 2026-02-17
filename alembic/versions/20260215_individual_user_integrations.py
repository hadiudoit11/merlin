"""Make organization_id nullable for individual user integrations

Revision ID: b9c8d7e6f5a4
Revises: a8b7c6d5e4f3
Create Date: 2026-02-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9c8d7e6f5a4'
down_revision: Union[str, None] = 'a8b7c6d5e4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Make organization_id nullable on integrations table.

    This allows individual users (without org membership) to connect integrations.

    Three modes now supported:
    - Individual: organization_id=NULL, user_id=SET
    - Org-level: organization_id=SET, user_id=NULL
    - Personal override: organization_id=SET, user_id=SET
    """
    # Make organization_id nullable
    op.alter_column(
        'integrations',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=True
    )

    # Add index on organization_id for better query performance
    op.create_index(
        'ix_integrations_organization_id',
        'integrations',
        ['organization_id'],
        unique=False
    )


def downgrade() -> None:
    # Remove index
    op.drop_index('ix_integrations_organization_id', table_name='integrations')

    # Note: This will fail if there are any integrations with NULL organization_id
    # You may need to delete those or assign them to an org first
    op.alter_column(
        'integrations',
        'organization_id',
        existing_type=sa.Integer(),
        nullable=False
    )
