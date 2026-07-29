"""add_paper_account_and_stop_loss_take_profit

Revision ID: abc123def456
Revises: 15c271df5928
Create Date: 2026-07-29 23:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'abc123def456'
down_revision: Union[str, Sequence[str], None] = '15c271df5928'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add stop_loss / take_profit to positions ---
    op.add_column(
        "positions",
        sa.Column("stop_loss", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column("take_profit", sa.Numeric(18, 8), nullable=True),
    )

    # --- Create paper_accounts table ---
    op.create_table(
        "paper_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "initial_balance",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("100000.00"),
        ),
        sa.Column(
            "current_balance",
            sa.Numeric(18, 2),
            nullable=False,
            server_default=sa.text("100000.00"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("paper_accounts")
    op.drop_column("positions", "take_profit")
    op.drop_column("positions", "stop_loss")
