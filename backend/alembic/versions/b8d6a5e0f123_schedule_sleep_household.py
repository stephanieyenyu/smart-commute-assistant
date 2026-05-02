"""Schedule, sleep, and household dashboard fields

Revision ID: b8d6a5e0f123
Revises: a7c3d2f1e905
Create Date: 2026-05-02 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8d6a5e0f123"
down_revision: Union[str, Sequence[str], None] = "a7c3d2f1e905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade() -> None:
    user_columns = _column_names("users")
    if "household_id" not in user_columns:
        op.add_column("users", sa.Column("household_id", sa.String(), nullable=True))
        op.create_index(op.f("ix_users_household_id"), "users", ["household_id"], unique=False)

    profile_columns = _column_names("commute_profiles")
    if "active_weekdays" not in profile_columns:
        op.add_column("commute_profiles", sa.Column("active_weekdays", sa.JSON(), nullable=True))

    override_columns = _column_names("commute_overrides")
    if "commute_disabled" not in override_columns:
        op.add_column("commute_overrides", sa.Column("commute_disabled", sa.Boolean(), nullable=True))
    if "commute_enabled" not in override_columns:
        op.add_column("commute_overrides", sa.Column("commute_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    override_columns = _column_names("commute_overrides")
    if "commute_enabled" in override_columns:
        op.drop_column("commute_overrides", "commute_enabled")
    if "commute_disabled" in override_columns:
        op.drop_column("commute_overrides", "commute_disabled")

    profile_columns = _column_names("commute_profiles")
    if "active_weekdays" in profile_columns:
        op.drop_column("commute_profiles", "active_weekdays")

    user_columns = _column_names("users")
    if "household_id" in user_columns:
        op.drop_index(op.f("ix_users_household_id"), table_name="users")
        op.drop_column("users", "household_id")
