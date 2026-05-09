"""restore multi schedule and family dashboard schema

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
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


def _foreign_key_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table_name) if fk.get("name")}


def _unique_constraint_names(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if constraint.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if not _has_table("households"):
        op.create_table(
            "households",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("invite_code", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    household_indexes = _index_names("households")
    if "ix_households_id" not in household_indexes:
        op.create_index("ix_households_id", "households", ["id"])
    if "ix_households_invite_code" not in household_indexes:
        op.create_index("ix_households_invite_code", "households", ["invite_code"], unique=True)

    if not _has_column("users", "display_name"):
        op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    if not _has_column("users", "household_id"):
        op.add_column("users", sa.Column("household_id", sa.Integer(), nullable=True))
    if "ix_users_household_id" not in _index_names("users"):
        op.create_index("ix_users_household_id", "users", ["household_id"])
    if dialect != "sqlite" and "fk_users_household_id" not in _foreign_key_names("users"):
        op.create_foreign_key("fk_users_household_id", "users", "households", ["household_id"], ["id"])

    if dialect == "postgresql":
        op.execute("ALTER TABLE commute_schedules DROP CONSTRAINT IF EXISTS uq_commute_schedules_user_id")
    if not _has_column("commute_schedules", "is_active"):
        op.add_column(
            "commute_schedules",
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        )
    if dialect != "sqlite" and "uq_commute_schedules_user_destination" not in _unique_constraint_names("commute_schedules"):
        op.create_unique_constraint(
            "uq_commute_schedules_user_destination",
            "commute_schedules",
            ["user_id", "dest_name"],
        )

    if not _has_table("commute_overrides"):
        op.create_table(
            "commute_overrides",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("schedule_id", sa.Integer(), sa.ForeignKey("commute_schedules.id"), nullable=True),
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("target_arrival_time", sa.String(), nullable=True),
            sa.Column("transport_mode_override", sa.String(), nullable=True),
            sa.Column("frozen_plan_key", sa.String(), nullable=True),
            sa.Column("frozen_departure_time", sa.String(), nullable=True),
            sa.Column("frozen_reminder_text", sa.Text(), nullable=True),
            sa.Column("reminder_prepared_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sent_plan_key", sa.String(), nullable=True),
            sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "target_date", "schedule_id", name="uq_commute_overrides_user_date_schedule"),
        )
    elif not _has_column("commute_overrides", "schedule_id"):
        if dialect == "postgresql":
            op.execute("ALTER TABLE commute_overrides DROP CONSTRAINT IF EXISTS uq_commute_overrides_user_date")
        op.add_column("commute_overrides", sa.Column("schedule_id", sa.Integer(), nullable=True))

    override_indexes = _index_names("commute_overrides")
    if "ix_commute_overrides_schedule_id" not in override_indexes:
        op.create_index("ix_commute_overrides_schedule_id", "commute_overrides", ["schedule_id"])
    if dialect != "sqlite" and "fk_commute_overrides_schedule_id" not in _foreign_key_names("commute_overrides"):
        op.create_foreign_key(
            "fk_commute_overrides_schedule_id",
            "commute_overrides",
            "commute_schedules",
            ["schedule_id"],
            ["id"],
        )
    if dialect != "sqlite" and "uq_commute_overrides_user_date_schedule" not in _unique_constraint_names("commute_overrides"):
        op.create_unique_constraint(
            "uq_commute_overrides_user_date_schedule",
            "commute_overrides",
            ["user_id", "target_date", "schedule_id"],
        )


def downgrade() -> None:
    op.drop_constraint("uq_commute_overrides_user_date_schedule", "commute_overrides", type_="unique")
    op.drop_constraint("fk_commute_overrides_schedule_id", "commute_overrides", type_="foreignkey")
    op.drop_index("ix_commute_overrides_schedule_id", table_name="commute_overrides")
    op.drop_column("commute_overrides", "schedule_id")
    op.create_unique_constraint("uq_commute_overrides_user_date", "commute_overrides", ["user_id", "target_date"])

    op.drop_constraint("uq_commute_schedules_user_destination", "commute_schedules", type_="unique")
    op.drop_column("commute_schedules", "is_active")
    op.create_unique_constraint("uq_commute_schedules_user_id", "commute_schedules", ["user_id"])

    op.drop_constraint("fk_users_household_id", "users", type_="foreignkey")
    op.drop_index("ix_users_household_id", table_name="users")
    op.drop_column("users", "household_id")
    op.drop_column("users", "display_name")

    op.drop_index("ix_households_invite_code", table_name="households")
    op.drop_index("ix_households_id", table_name="households")
    op.drop_table("households")
