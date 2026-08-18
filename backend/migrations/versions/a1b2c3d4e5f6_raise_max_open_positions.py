"""raise max_open_positions to 10

Revision ID: a1b2c3d4e5f6
Revises: f6a1b2c3d4e5
Create Date: 2026-08-11 17:00:00.000000

Backfills ``risk_settings.max_open_positions`` from 5 to 10 for existing
rows. The model default and the backtest engine both use a 10-position cap
(PR #27 changed the model default from 5 to 10), but the existing
``risk_settings`` row was seeded when the default was 5, so the live
auto-trading scheduler is currently hitting an unintended 5-position cap
that the OOS-validated strategy was never backtested with. This migration
aligns live execution with the backtested configuration (10 positions).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f6a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE risk_settings SET max_open_positions = 10 WHERE max_open_positions < 10")


def downgrade() -> None:
    op.execute("UPDATE risk_settings SET max_open_positions = 5 WHERE max_open_positions = 10")
