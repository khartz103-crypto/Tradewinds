"""Backtest routes — run strategy backtests and list backtestable strategies.

V1 intentionally has no persistence: a backtest run is computed on demand and
returned as JSON. No DB table is written.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.routers.strategies import (
    StrategyInfoResponse,
    list_strategies as list_db_strategies,
)
from app.services.backtest import (
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    run_backtest,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


# ── request / response models ──────────────────────────────────────────


class BacktestRunRequest(BaseModel):
    """Request body for ``POST /api/backtest/run``."""

    strategy_name: str
    symbols: list[str] = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    config_overrides: dict | None = None
    risk_per_trade: float = Field(0.005, gt=0, le=0.1)
    max_open_positions: int = Field(10, ge=1)
    max_portfolio_exposure_pct: float = Field(80.0, gt=0, le=1000)
    slippage: float = Field(0.0, ge=0, le=0.1)
    initial_balance: float = Field(100_000, gt=0)


class EquityPointResponse(BaseModel):
    """One equity-curve point."""

    date: datetime
    equity: float


class BacktestTradeResponse(BaseModel):
    """A completed backtest trade."""

    symbol: str
    side: str
    qty: float
    entry_price: float
    entry_date: datetime
    exit_price: float
    exit_date: datetime
    pnl: float
    r_multiple: float | None = None
    exit_reason: str


class BacktestResultResponse(BaseModel):
    """Full backtest result."""

    strategy_name: str
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    initial_balance: float
    final_balance: float
    equity_curve: list[EquityPointResponse]
    trades: list[BacktestTradeResponse]
    metrics: dict


def _to_response(result: BacktestResult) -> BacktestResultResponse:
    """Convert the service dataclasses into the API response model."""
    return BacktestResultResponse(
        strategy_name=result.strategy_name,
        symbols=result.symbols,
        start_date=result.start_date,
        end_date=result.end_date,
        initial_balance=float(result.initial_balance),
        final_balance=float(result.final_balance),
        equity_curve=[
            EquityPointResponse(date=point.date, equity=float(point.equity))
            for point in result.equity_curve
        ],
        trades=[
            BacktestTradeResponse(
                symbol=trade.symbol,
                side=trade.side,
                qty=float(trade.qty),
                entry_price=float(trade.entry_price),
                entry_date=trade.entry_date,
                exit_price=float(trade.exit_price),
                exit_date=trade.exit_date,
                pnl=float(trade.pnl),
                r_multiple=trade.r_multiple,
                exit_reason=trade.exit_reason,
            )
            for trade in result.trades
        ],
        metrics=result.metrics,
    )


# ── routes ─────────────────────────────────────────────────────────────


@router.post("/run", response_model=BacktestResultResponse)
async def run_backtest_endpoint(
    body: BacktestRunRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> BacktestResultResponse:
    """Run a backtest and return the equity curve, trades, and metrics."""
    if body.end_date <= body.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be after start_date",
        )
    try:
        result = await run_backtest(
            db,
            body.strategy_name,
            body.symbols,
            body.start_date,
            body.end_date,
            config_overrides=body.config_overrides,
            risk_per_trade=body.risk_per_trade,
            max_open_positions=body.max_open_positions,
            max_portfolio_exposure_pct=body.max_portfolio_exposure_pct,
            slippage=body.slippage,
            initial_balance=body.initial_balance,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _to_response(result)


@router.get("/strategies", response_model=list[StrategyInfoResponse])
async def list_backtest_strategies(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[StrategyInfoResponse]:
    """List enabled strategies that can be backtested.

    Reuses the canonical strategy listing from ``/api/strategies`` so the two
    endpoints can never drift apart.
    """
    return await list_db_strategies(db=db, _current_user=_current_user)
