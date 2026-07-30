"""add custom origin fields to commute_schedule_templates

NOTE (2026-07): this migration targets `commute_schedule_templates`, a table
from the abandoned "identity_multi_schedule" design (see
c1e2f3a4b506_identity_multi_schedule.py). That table is not created by the
live schema (models.py uses `commute_schedules` instead) and was never
reliably applied in production. Original revision/down_revision values were
also malformed (full filenames instead of short hashes), which orphaned this
migration from its intended parent. Fixed the IDs for consistency and turned
upgrade/downgrade into no-ops so this file can't accidentally run DDL against
a table that doesn't exist in production. Kept for history only.

Revision ID: a1b2c3d4e507
Revises: c1e2f3a4b506
Create Date: 2026-05-04
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e507"
down_revision = "c1e2f3a4b506"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
