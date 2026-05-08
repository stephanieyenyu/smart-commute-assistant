"""drop legacy unique schedule user index

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-08 09:20:00.000000

"""
from alembic import op


revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE commute_schedules DROP CONSTRAINT IF EXISTS ix_commute_schedules_user_id")
        op.execute("DROP INDEX IF EXISTS ix_commute_schedules_user_id")
        op.execute("CREATE INDEX IF NOT EXISTS ix_commute_schedules_user_id ON commute_schedules (user_id)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_commute_schedules_user_id")
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_commute_schedules_user_id ON commute_schedules (user_id)")
