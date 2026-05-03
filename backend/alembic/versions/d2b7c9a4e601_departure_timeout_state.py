"""Departure confirmation timeout state

Revision ID: d2b7c9a4e601
Revises: c1e2f3a4b506
Create Date: 2026-05-03 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2b7c9a4e601"
down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b506"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "commute_overrides" not in _table_names():
        return

    columns = _column_names("commute_overrides")
    if "departure_timeout_at" not in columns:
        op.add_column("commute_overrides", sa.Column("departure_timeout_at", sa.DateTime(timezone=True), nullable=True))
    if "departure_timeout_silent" not in columns:
        op.add_column("commute_overrides", sa.Column("departure_timeout_silent", sa.Boolean(), nullable=False, server_default=sa.text("false")))
        op.execute("UPDATE commute_overrides SET departure_timeout_silent = false WHERE departure_timeout_silent IS NULL")


def downgrade() -> None:
    if "commute_overrides" not in _table_names():
        return

    columns = _column_names("commute_overrides")
    if "departure_timeout_silent" in columns:
        op.drop_column("commute_overrides", "departure_timeout_silent")
    if "departure_timeout_at" in columns:
        op.drop_column("commute_overrides", "departure_timeout_at")
