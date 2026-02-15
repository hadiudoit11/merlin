"""Add user_id to integrations for hybrid auth

Revision ID: a8b7c6d5e4f3
Revises: f5c42c878153
Create Date: 2026-02-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b7c6d5e4f3'
down_revision: Union[str, None] = 'f5c42c878153'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add user_id column to integrations table for hybrid auth support.

    - user_id = NULL: Organization-level integration (shared by all members)
    - user_id = <id>: User-specific personal integration (overrides org)
    """
    op.add_column(
        'integrations',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    )


def downgrade() -> None:
    op.drop_column('integrations', 'user_id')
