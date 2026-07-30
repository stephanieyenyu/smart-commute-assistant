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
    # 注意：household_id 欄位改由 b2c3d4e5f6a7_restore_multi_schedule_family_dashboard
    # 以 Integer + FK(households.id) 的正確型別建立（與 models.py 及 production 實際 schema 一致）。
    # 這裡原本會先以 String 型別建立同名欄位，導致合併分支後 FK 建立時型別衝突，故移除。

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
