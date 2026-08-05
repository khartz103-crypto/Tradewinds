"""Risk settings model."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    max_risk_per_trade_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("1.00"), nullable=False
    )
    max_open_positions: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_daily_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("3.00"), nullable=False
    )
    max_portfolio_exposure_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("80.00"), nullable=False
    )
    circuit_breaker_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    circuit_breaker_loss_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("10.00"), nullable=False
    )
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
        return f"<RiskSettings user_id={self.user_id}>"
