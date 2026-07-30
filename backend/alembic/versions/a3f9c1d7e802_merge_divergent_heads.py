"""merge divergent migration heads (2026-07 cleanup)

Background: this project's migration history forked into two branches right
after the initial migration and never merged:

  * Branch A (the branch actually reflected in models.py / production):
    6f9faf62219a -> a1b2c3d4e5f6 -> b2c3d4e5f6a7 -> c3d4e5f6a7b8
    -> d4e5f6a7b8c9 -> e5f6a7b8c9d0 -> f6a7b8c9d0e1

  * Branch B ("identity_multi_schedule" exploration, built around a
    commute_schedule_templates table that is NOT part of the live schema):
    6f9faf62219a -> f2d4c6a8b901 -> a7c3d2f1e905 -> b8d6a5e0f123
    -> c1e2f3a4b506 -> {d2b7c9a4e601, e9a1d4c7b321, a1b2c3d4e507}

`render.yaml` never ran `alembic upgrade` on deploy, so production schema
drift was instead patched by hand in `app.main.ensure_runtime_schema()`.
Because the true state of the production database relative to either
branch is unknown, this merge intentionally does NOT replay any of the
divergent branch B DDL (some of it targets a table that doesn't exist in
production). It only joins the four existing heads into one so that
`alembic heads` / `alembic upgrade head` stop failing with "multiple heads".

Deployment note: apply this with `alembic stamp <this revision>` rather
than `alembic upgrade head`, so no old branch-B DDL is executed against a
database whose real state is unverified. Columns that branch A/B migrations
would have added to still-live tables (commute_overrides, commute_profiles,
users) are already covered by `ensure_runtime_schema()` as a safety net.

Revision ID: a3f9c1d7e802
Revises: f6a7b8c9d0e1, d2b7c9a4e601, e9a1d4c7b321, a1b2c3d4e507
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa


revision = "a3f9c1d7e802"
down_revision = ("f6a7b8c9d0e1", "d2b7c9a4e601", "e9a1d4c7b321", "a1b2c3d4e507")
branch_labels = None
depends_on = None


def upgrade():
    # Intentionally a no-op merge. See module docstring.
    pass


def downgrade():
    # Intentionally a no-op merge. See module docstring.
    pass
