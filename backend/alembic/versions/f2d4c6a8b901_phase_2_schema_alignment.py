"""Phase 2 schema alignment

Revision ID: f2d4c6a8b901
Revises: 6f9faf62219a
Create Date: 2026-05-02 00:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2d4c6a8b901"
down_revision: Union[str, Sequence[str], None] = "6f9faf62219a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if table_name not in _table_names():
        return
    if column.name in _column_names(table_name):
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    tables = _table_names()

    if "commute_overrides" not in tables:
        op.create_table(
            "commute_overrides",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("target_arrival_time", sa.String(), nullable=True),
            sa.Column("transport_mode_override", sa.String(), nullable=True),
            sa.Column("frozen_plan_key", sa.String(), nullable=True),
            sa.Column("frozen_departure_time", sa.String(), nullable=True),
            sa.Column("frozen_reminder_text", sa.Text(), nullable=True),
            sa.Column("reminder_prepared_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sent_plan_key", sa.String(), nullable=True),
            sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("nightly_brief_plan_key", sa.String(), nullable=True),
            sa.Column("nightly_brief_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("watchdog_alert_key", sa.String(), nullable=True),
            sa.Column("watchdog_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_date", name="uq_commute_overrides_user_date"),
        )
        op.create_index(op.f("ix_commute_overrides_id"), "commute_overrides", ["id"], unique=False)
        op.create_index(op.f("ix_commute_overrides_target_date"), "commute_overrides", ["target_date"], unique=False)
        op.create_index(op.f("ix_commute_overrides_user_id"), "commute_overrides", ["user_id"], unique=False)

    user_columns = [
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in user_columns:
        _add_column_if_missing("users", column)

    profile_columns = [
        sa.Column("home_township", sa.String(), nullable=True),
        sa.Column("home_place_name", sa.String(), nullable=True),
        sa.Column("office_township", sa.String(), nullable=True),
        sa.Column("office_place_name", sa.String(), nullable=True),
        sa.Column("selected_bus_stop_id", sa.String(), nullable=True),
        sa.Column("selected_bus_stop_name", sa.String(), nullable=True),
        sa.Column("selected_bus_stop_lat", sa.Float(), nullable=True),
        sa.Column("selected_bus_stop_lng", sa.Float(), nullable=True),
        sa.Column("selected_metro_station_id", sa.String(), nullable=True),
        sa.Column("selected_metro_station_name", sa.String(), nullable=True),
        sa.Column("selected_metro_station_lat", sa.Float(), nullable=True),
        sa.Column("selected_metro_station_lng", sa.Float(), nullable=True),
        sa.Column("last_computed_walk_to_bus_stop_min", sa.Integer(), nullable=True),
        sa.Column("last_computed_walk_to_metro_min", sa.Integer(), nullable=True),
        sa.Column("preferred_mode", sa.String(), nullable=True),
        sa.Column("transport_preference", sa.JSON(), nullable=True),
        sa.Column("max_walk_mins", sa.Integer(), nullable=True),
        sa.Column("reminder_enabled", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in profile_columns:
        _add_column_if_missing("commute_profiles", column)

    commute_log_columns = [
        sa.Column("selection_source", sa.String(), nullable=True),
        sa.Column("recommended_mode", sa.String(), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column("weather_buffer_minutes", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    ]
    for column in commute_log_columns:
        _add_column_if_missing("commute_logs", column)


def downgrade() -> None:
    # Non-destructive migration: keep data-bearing columns and tables intact.
    pass
