"""Pydantic schemas for market data responses."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class Bar(BaseModel):
    """A single OHLCV bar (candlestick)."""

    timestamp: datetime
    open: Decimal = Field(..., ge=0)
    high: Decimal = Field(..., ge=0)
    low: Decimal = Field(..., ge=0)
    close: Decimal = Field(..., ge=0)
    volume: int = Field(..., ge=0)

    model_config = {"from_attributes": True}


class Quote(BaseModel):
    """Latest quote (bid/ask/last) for a symbol."""

    symbol: str
    bid: Decimal | None = None
    ask: Decimal | None = None
    last: Decimal | None = None
    timestamp: datetime

    model_config = {"from_attributes": True}


class Snapshot(BaseModel):
    """Combined snapshot for a symbol — latest quote + daily bar + change."""

    symbol: str
    latest_quote: Quote | None = None
    latest_bar: Bar | None = None
    daily_change_pct: Decimal | None = None

    model_config = {"from_attributes": True}
