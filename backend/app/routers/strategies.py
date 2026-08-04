"""Strategy routes — list strategies, scan symbols, get details, scheduler control."""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.strategy import Strategy
from app.models.user import User
from app.services import scheduler as scheduler_service
from app.services.auto_trade import auto_trade_signals
from app.services.strategy_engine import run_strategy
from app.strategies import StrategySignal as StrategySignalDC

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


# ── Pydantic models ────────────────────────────────────────────────────────


class StrategyInfoResponse(BaseModel):
    """Public strategy metadata for API consumers."""

    id: str
    name: str
    display_name: str
    description: str
    is_enabled: bool
    config: dict

    model_config = {"from_attributes": True}


class StrategyScanRequest(BaseModel):
    """Request body for running a strategy scan."""

    symbols: list[str]
    auto_trade: bool = False
    position_size: float | None = None


class StrategySignalResponse(BaseModel):
    """Pydantic-serialisable version of a ``StrategySignal`` dataclass."""

    symbol: str
    action: str
    confidence: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reasoning: str = ""
    indicators: dict = {}
    error: str | None = None

    model_config = {"from_attributes": True}


class PositionOpenedResponse(BaseModel):
    """Result of attempting to open a paper position for a signal."""

    symbol: str
    side: str | None = None
    quantity: Decimal | None = None
    entry_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    position_id: str | None = None
    error: str | None = None


class StrategyScanResponse(BaseModel):
    """Response wrapper for scans — signals plus auto-trade outcomes."""

    signals: list[StrategySignalResponse]
    positions_opened: list[PositionOpenedResponse]


class SchedulerStatusResponse(BaseModel):
    """Status of the scheduled auto-trading scanner."""

    running: bool
    interval_seconds: int
    default_symbols: list[str]
    last_run: str | None = None
    last_summary: dict | None = None


# ── helpers ────────────────────────────────────────────────────────────────


def _signal_dc_to_response(signal: StrategySignalDC) -> StrategySignalResponse:
    """Convert a dataclass ``StrategySignal`` to a Pydantic response model."""
    return StrategySignalResponse(
        symbol=signal.symbol,
        action=signal.action,
        confidence=signal.confidence,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        reasoning=signal.reasoning,
        indicators=signal.indicators,
        error=signal.error,
    )


def _opened_dc_to_response(result: dict) -> PositionOpenedResponse:
    """Convert an auto-trade result dict to its Pydantic response model."""
    return PositionOpenedResponse(
        symbol=result.get("symbol", ""),
        side=result.get("side"),
        quantity=result.get("quantity"),
        entry_price=result.get("entry_price"),
        stop_loss=result.get("stop_loss"),
        take_profit=result.get("take_profit"),
        position_id=result.get("position_id"),
        error=result.get("error"),
    )


async def _get_strategy_id(db: AsyncSession, name: str) -> UUID | None:
    """Look up the DB id for a strategy by name (None if unknown/disabled)."""
    result = await db.execute(
        select(Strategy).where(
            Strategy.name == name,
            Strategy.is_enabled == True,  # noqa: E712
        )
    )
    strategy = result.scalar_one_or_none()
    return strategy.id if strategy is not None else None


# ── routes ─────────────────────────────────────────────────────────────────


@router.get("", response_model=list[StrategyInfoResponse])
async def list_strategies(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[StrategyInfoResponse]:
    """Return all enabled strategies."""
    result = await db.execute(
        select(Strategy).where(Strategy.is_enabled == True)  # noqa: E712
    )
    strategies = result.scalars().all()
    return [
        StrategyInfoResponse(
            id=str(s.id),
            name=s.name,
            display_name=s.display_name,
            description=s.description,
            is_enabled=s.is_enabled,
            config=s.config or {},
        )
        for s in strategies
    ]


@router.get("/{name}", response_model=StrategyInfoResponse)
async def get_strategy_details(
    name: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> StrategyInfoResponse:
    """Return details for a single strategy by name."""
    result = await db.execute(
        select(Strategy).where(Strategy.name == name)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{name}' not found",
        )
    return StrategyInfoResponse(
        id=str(strategy.id),
        name=strategy.name,
        display_name=strategy.display_name,
        description=strategy.description,
        is_enabled=strategy.is_enabled,
        config=strategy.config or {},
    )


@router.post("/{name}/scan", response_model=StrategyScanResponse)
async def scan(
    name: str,
    body: StrategyScanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StrategyScanResponse:
    """Run a named strategy against the given list of symbols.

    When ``body.auto_trade`` is true, tradeable signals (clean, buy/sell)
    are automatically turned into paper positions via the paper trading
    engine. Symbols that already have an open position are skipped, and the
    default position size is 10% of the paper account cash balance unless
    ``body.position_size`` (in dollars) is given.
    """
    if not body.symbols:
        return StrategyScanResponse(signals=[], positions_opened=[])

    try:
        signals: list[StrategySignalDC] = await run_strategy(db, name, body.symbols)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Strategy scan failed: {exc}",
        )

    positions_opened: list[PositionOpenedResponse] = []
    if body.auto_trade:
        strategy_id = await _get_strategy_id(db, name)
        results = await auto_trade_signals(
            db,
            user_id=current_user.id,
            signals=signals,
            strategy_id=strategy_id,
            position_size=body.position_size,
            strategy_name=name,
        )
        positions_opened = [_opened_dc_to_response(r) for r in results]

    return StrategyScanResponse(
        signals=[_signal_dc_to_response(s) for s in signals],
        positions_opened=positions_opened,
    )


# ── scheduled scanner control ──────────────────────────────────────────────


@router.post("/scheduler/start", response_model=SchedulerStatusResponse)
async def scheduler_start(
    _current_user: User = Depends(get_current_user),
) -> SchedulerStatusResponse:
    """Enable the scheduled auto-trading scanner (persisted in Redis)."""
    await scheduler_service.set_enabled(True)
    return SchedulerStatusResponse(**await scheduler_service.get_status())


@router.post("/scheduler/stop", response_model=SchedulerStatusResponse)
async def scheduler_stop(
    _current_user: User = Depends(get_current_user),
) -> SchedulerStatusResponse:
    """Disable the scheduled auto-trading scanner (persisted in Redis)."""
    await scheduler_service.set_enabled(False)
    return SchedulerStatusResponse(**await scheduler_service.get_status())


@router.get("/scheduler/status", response_model=SchedulerStatusResponse)
async def scheduler_status(
    _current_user: User = Depends(get_current_user),
) -> SchedulerStatusResponse:
    """Return the current scheduler status."""
    return SchedulerStatusResponse(**await scheduler_service.get_status())
