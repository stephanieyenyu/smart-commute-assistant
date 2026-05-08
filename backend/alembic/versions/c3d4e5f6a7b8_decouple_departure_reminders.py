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


def upgrade() -> None:
    op.add_column("commute_overrides", sa.Column("monitor_one_hour_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("commute_overrides", sa.Column("monitor_five_min_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("commute_overrides", sa.Column("departure_question_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("commute_overrides", sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("commute_overrides", "departed_at")
    op.drop_column("commute_overrides", "departure_question_sent_at")
    op.drop_column("commute_overrides", "monitor_five_min_sent_at")
    op.drop_column("commute_overrides", "monitor_one_hour_sent_at")
