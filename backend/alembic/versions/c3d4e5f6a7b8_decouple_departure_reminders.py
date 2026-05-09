"""decouple departure reminders and monitor state

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-08 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE commute_schedules DROP CONSTRAINT IF EXISTS uq_commute_schedules_user_destination")
        op.execute("DROP INDEX IF EXISTS uq_commute_schedules_user_destination")
    if not _has_column("commute_overrides", "monitor_one_hour_sent_at"):
        op.add_column("commute_overrides", sa.Column("monitor_one_hour_sent_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("commute_overrides", "monitor_five_min_sent_at"):
        op.add_column("commute_overrides", sa.Column("monitor_five_min_sent_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("commute_overrides", "departure_question_sent_at"):
        op.add_column("commute_overrides", sa.Column("departure_question_sent_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column("commute_overrides", "departed_at"):
        op.add_column("commute_overrides", sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("commute_overrides", "departed_at")
    op.drop_column("commute_overrides", "departure_question_sent_at")
    op.drop_column("commute_overrides", "monitor_five_min_sent_at")
    op.drop_column("commute_overrides", "monitor_one_hour_sent_at")
