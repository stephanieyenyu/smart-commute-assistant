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


def upgrade() -> None:
    op.create_table(
        "households",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invite_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_households_id", "households", ["id"])
    op.create_index("ix_households_invite_code", "households", ["invite_code"], unique=True)

    op.add_column("users", sa.Column("display_name", sa.String(), nullable=True))
    op.add_column("users", sa.Column("household_id", sa.Integer(), nullable=True))
    op.create_index("ix_users_household_id", "users", ["household_id"])
    op.create_foreign_key("fk_users_household_id", "users", "households", ["household_id"], ["id"])

    op.drop_constraint("uq_commute_schedules_user_id", "commute_schedules", type_="unique")
    op.add_column(
        "commute_schedules",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_commute_schedules_user_destination",
        "commute_schedules",
        ["user_id", "dest_name"],
    )

    op.drop_constraint("uq_commute_overrides_user_date", "commute_overrides", type_="unique")
    op.add_column("commute_overrides", sa.Column("schedule_id", sa.Integer(), nullable=True))
    op.create_index("ix_commute_overrides_schedule_id", "commute_overrides", ["schedule_id"])
    op.create_foreign_key(
        "fk_commute_overrides_schedule_id",
        "commute_overrides",
        "commute_schedules",
        ["schedule_id"],
        ["id"],
    )
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
