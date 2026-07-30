"""Add destination table and fixed template flag

Revision ID: e9a1d4c7b321
Revises: c1e2f3a4b506
Create Date: 2026-05-03 22:55:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9a1d4c7b321"
down_revision: Union[str, Sequence[str], None] = "c1e2f3a4b506"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    tables = _table_names()

    if "commute_destinations" not in tables:
        op.create_table(
            "commute_destinations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("address", sa.String(), nullable=True),
            sa.Column("lat", sa.Float(), nullable=True),
            sa.Column("lng", sa.Float(), nullable=True),
            sa.Column("city", sa.String(), nullable=True),
            sa.Column("township", sa.String(), nullable=True),
            sa.Column("place_name", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "label", name="uq_commute_destinations_user_label"),
        )
        op.create_index(op.f("ix_commute_destinations_id"), "commute_destinations", ["id"], unique=False)
        op.create_index(op.f("ix_commute_destinations_user_id"), "commute_destinations", ["user_id"], unique=False)

    if "commute_schedule_templates" in tables:
        template_columns = _column_names("commute_schedule_templates")
        if "destination_id" not in template_columns:
            op.add_column("commute_schedule_templates", sa.Column("destination_id", sa.Integer(), nullable=True))
            op.create_foreign_key(
                "fk_commute_schedule_templates_destination_id",
                "commute_schedule_templates",
                "commute_destinations",
                ["destination_id"],
                ["id"],
            )
            op.create_index(op.f("ix_commute_schedule_templates_destination_id"), "commute_schedule_templates", ["destination_id"], unique=False)
        if "is_fixed" not in template_columns:
            op.add_column("commute_schedule_templates", sa.Column("is_fixed", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    tables = _table_names()

    if "commute_schedule_templates" in tables:
        template_columns = _column_names("commute_schedule_templates")
        if "is_fixed" in template_columns:
            op.drop_column("commute_schedule_templates", "is_fixed")
        if "destination_id" in template_columns:
            op.drop_index(op.f("ix_commute_schedule_templates_destination_id"), table_name="commute_schedule_templates")
            op.drop_constraint("fk_commute_schedule_templates_destination_id", "commute_schedule_templates", type_="foreignkey")
            op.drop_column("commute_schedule_templates", "destination_id")

    if "commute_destinations" in tables:
        op.drop_index(op.f("ix_commute_destinations_user_id"), table_name="commute_destinations")
        op.drop_index(op.f("ix_commute_destinations_id"), table_name="commute_destinations")
        op.drop_table("commute_destinations")
