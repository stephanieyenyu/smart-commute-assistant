"""Departure confirmation state

Revision ID: a7c3d2f1e905
Revises: f2d4c6a8b901
Create Date: 2026-05-02 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c3d2f1e905"
down_revision: Union[str, Sequence[str], None] = "f2d4c6a8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    existing = _column_names("commute_overrides")
    columns = {
        "departure_confirmed_at": sa.Column("departure_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        "departure_check_sent_at": sa.Column("departure_check_sent_at", sa.DateTime(timezone=True), nullable=True),
        "departure_snoozed_until": sa.Column("departure_snoozed_until", sa.DateTime(timezone=True), nullable=True),
        "snooze_one_min_sent_at": sa.Column("snooze_one_min_sent_at", sa.DateTime(timezone=True), nullable=True),
        "snooze_departure_sent_at": sa.Column("snooze_departure_sent_at", sa.DateTime(timezone=True), nullable=True),
    }
    for name, column in columns.items():
        if name not in existing:
            op.add_column("commute_overrides", column)


def downgrade() -> None:
    existing = _column_names("commute_overrides")
    for name in (
        "snooze_departure_sent_at",
        "snooze_one_min_sent_at",
        "departure_snoozed_until",
        "departure_check_sent_at",
        "departure_confirmed_at",
    ):
        if name in existing:
            op.drop_column("commute_overrides", name)
