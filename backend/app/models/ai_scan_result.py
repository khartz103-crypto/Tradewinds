"""AI scan result model."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AIScanResult(Base):
    __tablename__ = "ai_scan_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    indicators: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recommended_entry: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    expected_holding: Mapped[str | None] = mapped_column(String(50), nullable=True)
    risk_reward_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(50), nullable=True)
    catalysts: Mapped[str | None] = mapped_column(Text, nullable=True)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return f"<AIScanResult {self.symbol} score={self.confidence_score}>"
