"""add alert_status, family_groups, family_members

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-08 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add alert_status to commute_overrides
    with op.batch_alter_table('commute_overrides') as batch_op:
        batch_op.add_column(sa.Column('alert_status', sa.String(), nullable=True))

    # 2) Create family_groups table
    op.create_table(
        'family_groups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('invite_token', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invite_token', name='uq_family_groups_invite_token'),
    )
    op.create_index('ix_family_groups_id', 'family_groups', ['id'])
    op.create_index('ix_family_groups_invite_token', 'family_groups', ['invite_token'])

    # 3) Create family_members table
    op.create_table(
        'family_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', sa.Integer(), sa.ForeignKey('family_groups.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('nickname', sa.String(), nullable=True),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_family_members_id', 'family_members', ['id'])
    op.create_index('ix_family_members_group_id', 'family_members', ['group_id'])
    op.create_index('ix_family_members_user_id', 'family_members', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_family_members_user_id', table_name='family_members')
    op.drop_index('ix_family_members_group_id', table_name='family_members')
    op.drop_index('ix_family_members_id', table_name='family_members')
    op.drop_table('family_members')

    op.drop_index('ix_family_groups_invite_token', table_name='family_groups')
    op.drop_index('ix_family_groups_id', table_name='family_groups')
    op.drop_table('family_groups')

    with op.batch_alter_table('commute_overrides') as batch_op:
        batch_op.drop_column('alert_status')
