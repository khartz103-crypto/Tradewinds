"""add regime_filter to strategies

Revision ID: f6a1b2c3d4e5
Revises: abc123def456
Create Date: 2026-08-11 16:30:00.000000

Adds the optional ``regime_filter`` column to ``strategies`` so the live
strategy engine can apply the validated SPY>200-SMA regime gate
(OOS Sharpe 0.247 → 1.138, see /home/team/shared/regime_filter_spy200.md).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f6a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "abc123def456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "strategies",
        sa.Column("regime_filter", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("strategies", "regime_filter")
