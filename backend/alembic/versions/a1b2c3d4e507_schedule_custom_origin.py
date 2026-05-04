"""add custom origin fields to commute_schedule_templates

Revision ID: a1b2c3d4e507_schedule_custom_origin
Revises: c1e2f3a4b506_identity_multi_schedule
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e507_schedule_custom_origin"
down_revision = "c1e2f3a4b506_identity_multi_schedule"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("commute_schedule_templates", sa.Column("origin_address", sa.String(), nullable=True))
    op.add_column("commute_schedule_templates", sa.Column("origin_lat", sa.Float(), nullable=True))
    op.add_column("commute_schedule_templates", sa.Column("origin_lng", sa.Float(), nullable=True))
    op.add_column("commute_schedule_templates", sa.Column("origin_city", sa.String(), nullable=True))
    op.add_column("commute_schedule_templates", sa.Column("origin_township", sa.String(), nullable=True))
    op.add_column("commute_schedule_templates", sa.Column("origin_place_name", sa.String(), nullable=True))


def downgrade():
    op.drop_column("commute_schedule_templates", "origin_place_name")
    op.drop_column("commute_schedule_templates", "origin_township")
    op.drop_column("commute_schedule_templates", "origin_city")
    op.drop_column("commute_schedule_templates", "origin_lng")
    op.drop_column("commute_schedule_templates", "origin_lat")
    op.drop_column("commute_schedule_templates", "origin_address")
