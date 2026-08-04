"""Pydantic schemas for paper trading API responses."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PositionResponse(BaseModel):
    """All position fields for API output."""

    id: UUID
    user_id: UUID
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    status: str
    strategy_id: UUID | None = None
    entry_date: datetime
    exit_date: datetime | None = None
    exit_price: Decimal | None = None
    pnl: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PortfolioSummaryResponse(BaseModel):
    """Portfolio summary for the dashboard."""

    account_value: Decimal = Field(..., description="Cash + sum of open position values")
    buying_power: Decimal = Field(..., description="Available cash")
    today_pnl: Decimal = Field(..., description="Today's P&L across all positions")
    total_return_pct: Decimal = Field(..., description="Overall return percentage since inception")
    open_positions: int = Field(..., description="Count of currently open positions")
    daily_pnl: Decimal = Field(..., description="Today's realized + unrealized P&L")
    circuit_breaker_active: bool = Field(..., description="Whether the daily loss circuit breaker has tripped")
    initial_balance: Decimal = Field(..., description="Starting account balance")
    current_balance: Decimal = Field(..., description="Current cash balance")

    model_config = {"from_attributes": True}


class PerformanceResponse(BaseModel):
    realized_pnl: Decimal
    win_rate: Decimal
    total_trades_closed: int
    open_positions: int
    current_equity: Decimal


class TradeResponse(BaseModel):
    """All trade fields for API output."""

    id: UUID
    user_id: UUID
    position_id: UUID | None = None
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    order_type: str
    status: str
    filled_at: datetime | None = None
    strategy_id: UUID | None = None
    is_paper: bool = True
    created_at: datetime

    model_config = {"from_attributes": True}
