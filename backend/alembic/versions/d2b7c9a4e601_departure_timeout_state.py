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


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    tables = _table_names()
    if "commute_overrides" in tables:
        columns = _column_names("commute_overrides")
        if "departure_timeout_at" not in columns:
            op.add_column("commute_overrides", sa.Column("departure_timeout_at", sa.DateTime(timezone=True), nullable=True))
        if "departure_timeout_silent" not in columns:
            op.add_column("commute_overrides", sa.Column("departure_timeout_silent", sa.Boolean(), nullable=False, server_default=sa.text("false")))
            op.execute("UPDATE commute_overrides SET departure_timeout_silent = false WHERE departure_timeout_silent IS NULL")

        override_indexes = _index_names("commute_overrides")
        if "ix_commute_overrides_target_date_frozen_departure" not in override_indexes:
            op.create_index(
                "ix_commute_overrides_target_date_frozen_departure",
                "commute_overrides",
                ["target_date", "frozen_departure_time"],
                unique=False,
            )

    if "users" in tables:
        user_indexes = _index_names("users")
        if "ix_users_household_id_id" not in user_indexes:
            op.create_index("ix_users_household_id_id", "users", ["household_id", "id"], unique=False)


def downgrade() -> None:
    tables = _table_names()
    if "commute_overrides" in tables:
        columns = _column_names("commute_overrides")
        indexes = _index_names("commute_overrides")
        if "ix_commute_overrides_target_date_frozen_departure" in indexes:
            op.drop_index("ix_commute_overrides_target_date_frozen_departure", table_name="commute_overrides")
        if "departure_timeout_silent" in columns:
            op.drop_column("commute_overrides", "departure_timeout_silent")
        if "departure_timeout_at" in columns:
            op.drop_column("commute_overrides", "departure_timeout_at")
    if "users" in tables and "ix_users_household_id_id" in _index_names("users"):
        op.drop_index("ix_users_household_id_id", table_name="users")
