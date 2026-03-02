"""merge heads

Revision ID: 9d180b08e9db
Revises: 3c848a27743d, a1b2c3d4e5f6
Create Date: 2026-02-21 00:44:29.464654

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d180b08e9db'
down_revision: Union[str, None] = ('3c848a27743d', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
