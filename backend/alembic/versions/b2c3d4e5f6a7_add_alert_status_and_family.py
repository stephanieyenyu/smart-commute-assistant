"""add alert_status, family_groups, family_members

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-08 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    # 1) Add alert_status to commute_overrides
    if not _has_column('commute_overrides', 'alert_status'):
        with op.batch_alter_table('commute_overrides') as batch_op:
            batch_op.add_column(sa.Column('alert_status', sa.String(), nullable=True))

    # 2) Create family_groups table
    if not _has_table('family_groups'):
        op.create_table(
            'family_groups',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('invite_token', sa.String(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('invite_token', name='uq_family_groups_invite_token'),
        )
    family_group_indexes = _index_names('family_groups')
    if 'ix_family_groups_id' not in family_group_indexes:
        op.create_index('ix_family_groups_id', 'family_groups', ['id'])
    if 'ix_family_groups_invite_token' not in family_group_indexes:
        op.create_index('ix_family_groups_invite_token', 'family_groups', ['invite_token'])

    # 3) Create family_members table
    if not _has_table('family_members'):
        op.create_table(
            'family_members',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('group_id', sa.Integer(), sa.ForeignKey('family_groups.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('nickname', sa.String(), nullable=True),
            sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
    family_member_indexes = _index_names('family_members')
    if 'ix_family_members_id' not in family_member_indexes:
        op.create_index('ix_family_members_id', 'family_members', ['id'])
    if 'ix_family_members_group_id' not in family_member_indexes:
        op.create_index('ix_family_members_group_id', 'family_members', ['group_id'])
    if 'ix_family_members_user_id' not in family_member_indexes:
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
