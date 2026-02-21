"""Rename integrations to skills

Revision ID: a1b2c3d4e5f6
Revises: 3d57c39e0d42
Create Date: 2026-02-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str = '3d57c39e0d42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename tables
    op.rename_table('integrations', 'skills')
    op.rename_table('space_integrations', 'space_skills')

    # Rename columns in space_skills (was space_integrations)
    with op.batch_alter_table('space_skills') as batch_op:
        batch_op.alter_column('integration_id', new_column_name='skill_id')

    # Rename columns in meeting_imports
    with op.batch_alter_table('meeting_imports') as batch_op:
        batch_op.alter_column('integration_id', new_column_name='skill_id')

    # Rename columns in input_events
    with op.batch_alter_table('input_events') as batch_op:
        batch_op.alter_column('integration_id', new_column_name='skill_id')

    # Rename columns in page_syncs
    with op.batch_alter_table('page_syncs') as batch_op:
        batch_op.alter_column('space_integration_id', new_column_name='space_skill_id')

    # Rename column in organizations
    with op.batch_alter_table('organizations') as batch_op:
        batch_op.alter_column('integration_settings', new_column_name='skill_settings')

    # Update node_type values in nodes table
    op.execute("UPDATE nodes SET node_type = 'skill' WHERE node_type = 'integration'")


def downgrade() -> None:
    # Revert node_type values
    op.execute("UPDATE nodes SET node_type = 'integration' WHERE node_type = 'skill'")

    # Revert column renames
    with op.batch_alter_table('organizations') as batch_op:
        batch_op.alter_column('skill_settings', new_column_name='integration_settings')

    with op.batch_alter_table('page_syncs') as batch_op:
        batch_op.alter_column('space_skill_id', new_column_name='space_integration_id')

    with op.batch_alter_table('input_events') as batch_op:
        batch_op.alter_column('skill_id', new_column_name='integration_id')

    with op.batch_alter_table('meeting_imports') as batch_op:
        batch_op.alter_column('skill_id', new_column_name='integration_id')

    with op.batch_alter_table('space_skills') as batch_op:
        batch_op.alter_column('skill_id', new_column_name='integration_id')

    # Revert table renames
    op.rename_table('space_skills', 'space_integrations')
    op.rename_table('skills', 'integrations')
