"""Identity labels and multi schedule templates

Revision ID: c1e2f3a4b506
Revises: b8d6a5e0f123
Create Date: 2026-05-03 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1e2f3a4b506"
down_revision: Union[str, Sequence[str], None] = "b8d6a5e0f123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    tables = _table_names()

    if "commute_profiles" in tables:
        profile_columns = _column_names("commute_profiles")
        if "identity_type" not in profile_columns:
            op.add_column("commute_profiles", sa.Column("identity_type", sa.String(), nullable=True))
        if "destination_label" not in profile_columns:
            op.add_column("commute_profiles", sa.Column("destination_label", sa.String(), nullable=True))
        op.execute("UPDATE commute_profiles SET identity_type = 'worker' WHERE identity_type IS NULL")
        op.execute("UPDATE commute_profiles SET destination_label = '公司' WHERE destination_label IS NULL")

    if "commute_schedule_templates" not in tables:
        op.create_table(
            "commute_schedule_templates",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("target_arrival_time", sa.String(), nullable=False),
            sa.Column("destination_label", sa.String(), nullable=False),
            sa.Column("active_weekdays", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_commute_schedule_templates_id"), "commute_schedule_templates", ["id"], unique=False)
        op.create_index(op.f("ix_commute_schedule_templates_user_id"), "commute_schedule_templates", ["user_id"], unique=False)


def downgrade() -> None:
    tables = _table_names()
    if "commute_schedule_templates" in tables:
        op.drop_index(op.f("ix_commute_schedule_templates_user_id"), table_name="commute_schedule_templates")
        op.drop_index(op.f("ix_commute_schedule_templates_id"), table_name="commute_schedule_templates")
        op.drop_table("commute_schedule_templates")

    if "commute_profiles" in tables:
        profile_columns = _column_names("commute_profiles")
        if "destination_label" in profile_columns:
            op.drop_column("commute_profiles", "destination_label")
        if "identity_type" in profile_columns:
            op.drop_column("commute_profiles", "identity_type")
