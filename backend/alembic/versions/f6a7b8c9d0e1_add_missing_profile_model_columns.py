"""add missing profile model columns

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if not _has_column(table_name, column.name):
        op.add_column(table_name, column)


def _timestamp_column(name: str) -> sa.Column:
    if op.get_bind().dialect.name == "sqlite":
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    _add_column_if_missing("users", _timestamp_column("created_at"))

    _add_column_if_missing("commute_profiles", sa.Column("home_township", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("home_place_name", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("office_township", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("office_place_name", sa.String(), nullable=True))

    _add_column_if_missing("commute_profiles", sa.Column("selected_bus_stop_id", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_bus_stop_name", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_bus_stop_lat", sa.Float(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_bus_stop_lng", sa.Float(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_metro_station_id", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_metro_station_name", sa.String(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_metro_station_lat", sa.Float(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("selected_metro_station_lng", sa.Float(), nullable=True))

    _add_column_if_missing("commute_profiles", sa.Column("last_computed_walk_to_bus_stop_min", sa.Integer(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("last_computed_walk_to_metro_min", sa.Integer(), nullable=True))
    _add_column_if_missing("commute_profiles", sa.Column("preferred_mode", sa.String(), nullable=True))
    _add_column_if_missing(
        "commute_profiles",
        sa.Column("reminder_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    _add_column_if_missing("commute_profiles", _timestamp_column("created_at"))
    _add_column_if_missing("commute_profiles", _timestamp_column("updated_at"))


def downgrade() -> None:
    for column_name in (
        "updated_at",
        "created_at",
        "reminder_enabled",
        "preferred_mode",
        "last_computed_walk_to_metro_min",
        "last_computed_walk_to_bus_stop_min",
        "selected_metro_station_lng",
        "selected_metro_station_lat",
        "selected_metro_station_name",
        "selected_metro_station_id",
        "selected_bus_stop_lng",
        "selected_bus_stop_lat",
        "selected_bus_stop_name",
        "selected_bus_stop_id",
        "office_place_name",
        "office_township",
        "home_place_name",
        "home_township",
    ):
        if _has_column("commute_profiles", column_name):
            op.drop_column("commute_profiles", column_name)
    if _has_column("users", "created_at"):
        op.drop_column("users", "created_at")
