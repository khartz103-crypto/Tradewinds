"""Trading strategy model."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Optional market-regime gate applied to this strategy's signals by the
    #: strategy engine. ``None`` = disabled; ``"spy200sma"`` = skip signals
    #: when SPY closes at or below its 200-day SMA (validated in the
    #: 2026-08-11 backtest: OOS Sharpe 0.247 → 1.138).
    regime_filter: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Strategy {self.name}>"
