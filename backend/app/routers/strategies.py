"""Strategy routes — list strategies, scan symbols, get details."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.strategy import Strategy
from app.models.user import User
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


@router.post("/{name}/scan", response_model=list[StrategySignalResponse])
async def scan(
    name: str,
    body: StrategyScanRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[StrategySignalResponse]:
    """Run a named strategy against the given list of symbols."""
    if not body.symbols:
        return []

    try:
        signals: list[StrategySignalDC] = await run_strategy(db, name, body.symbols)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Strategy scan failed: {exc}",
        )

    return [_signal_dc_to_response(s) for s in signals]
