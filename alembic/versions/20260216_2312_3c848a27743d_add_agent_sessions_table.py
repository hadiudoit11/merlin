"""add_agent_sessions_table

Revision ID: 3c848a27743d
Revises: 3d57c39e0d42
Create Date: 2026-02-16 23:12:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3c848a27743d'
down_revision = '3d57c39e0d42'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_sessions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('canvas_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('messages', sa.JSON(), nullable=False),
        sa.Column('context_summary', sa.Text(), nullable=True),
        sa.Column('attached_files', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['canvas_id'], ['canvases.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_agent_sessions_user_id', 'agent_sessions', ['user_id'])
    op.create_index('ix_agent_sessions_canvas_id', 'agent_sessions', ['canvas_id'])


def downgrade() -> None:
    op.drop_index('ix_agent_sessions_canvas_id', 'agent_sessions')
    op.drop_index('ix_agent_sessions_user_id', 'agent_sessions')
    op.drop_table('agent_sessions')
