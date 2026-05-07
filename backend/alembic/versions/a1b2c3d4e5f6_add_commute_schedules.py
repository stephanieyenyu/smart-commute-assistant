"""add commute_schedules table

Revision ID: a1b2c3d4e5f6
Revises: 6f9faf62219a
Create Date: 2026-05-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '6f9faf62219a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'commute_schedules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('origin_name', sa.String(), nullable=True),
        sa.Column('origin_address', sa.String(), nullable=True),
        sa.Column('origin_lat', sa.Float(), nullable=True),
        sa.Column('origin_lng', sa.Float(), nullable=True),
        sa.Column('dest_name', sa.String(), nullable=True),
        sa.Column('dest_address', sa.String(), nullable=True),
        sa.Column('dest_lat', sa.Float(), nullable=True),
        sa.Column('dest_lng', sa.Float(), nullable=True),
        sa.Column('time', sa.String(), nullable=True),
        sa.Column('days', sa.JSON(), nullable=True),
        sa.Column('reminder_enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_commute_schedules_user_id'),
    )
    op.create_index('ix_commute_schedules_id', 'commute_schedules', ['id'])
    op.create_index('ix_commute_schedules_user_id', 'commute_schedules', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_commute_schedules_user_id', table_name='commute_schedules')
    op.drop_index('ix_commute_schedules_id', table_name='commute_schedules')
    op.drop_table('commute_schedules')
