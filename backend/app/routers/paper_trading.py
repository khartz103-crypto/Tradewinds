"""Paper trading routes — open/close positions, portfolio summary, trade history."""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.paper_trading import (
    PortfolioSummaryResponse,
    PositionResponse,
    TradeResponse,
)
from app.services.paper_trading import (
    close_position,
    get_closed_positions,
    get_open_positions,
    get_portfolio_summary,
    get_trade_history,
    open_position,
    update_positions,
)

router = APIRouter(prefix="/api/paper", tags=["paper_trading"])


class OpenPositionRequest(BaseModel):
    """Request body for opening a new paper position."""

    symbol: str = Field(..., min_length=1, max_length=20)
    side: str = Field(..., pattern="^(long|short)$")
    quantity: Decimal = Field(..., gt=0)
    stop_loss: Decimal | None = Field(None, gt=0)
    take_profit: Decimal | None = Field(None, gt=0)


def _position_to_response(p) -> PositionResponse:
    """Convert a Position ORM object to its Pydantic response schema."""
    return PositionResponse(
        id=p.id,
        user_id=p.user_id,
        symbol=p.symbol,
        side=p.side.value if hasattr(p.side, "value") else p.side,
        quantity=p.quantity,
        entry_price=p.entry_price,
        current_price=p.current_price,
        status=p.status.value if hasattr(p.status, "value") else p.status,
        strategy_id=p.strategy_id,
        entry_date=p.entry_date,
        exit_date=p.exit_date,
        exit_price=p.exit_price,
        pnl=p.pnl,
        stop_loss=p.stop_loss,
        take_profit=p.take_profit,
        notes=p.notes,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _trade_to_response(t) -> TradeResponse:
    """Convert a Trade ORM object to its Pydantic response schema."""
    return TradeResponse(
        id=t.id,
        user_id=t.user_id,
        position_id=t.position_id,
        symbol=t.symbol,
        side=t.side,
        quantity=t.quantity,
        price=t.price,
        order_type=t.order_type.value if hasattr(t.order_type, "value") else t.order_type,
        status=t.status.value if hasattr(t.status, "value") else t.status,
        filled_at=t.filled_at,
        strategy_id=t.strategy_id,
        is_paper=t.is_paper,
        created_at=t.created_at,
    )


@router.post("/positions", response_model=PositionResponse, status_code=status.HTTP_201_CREATED)
async def open_new_position(
    body: OpenPositionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PositionResponse:
    """Open a new paper-trading position with risk checks."""
    try:
        position = await open_position(
            db=db,
            user_id=current_user.id,
            symbol=body.symbol,
            side=body.side,
            quantity=body.quantity,
            stop_loss=body.stop_loss,
            take_profit=body.take_profit,
        )
        return _position_to_response(position)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.post("/positions/{position_id}/close", response_model=PositionResponse)
async def close_single_position(
    position_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PositionResponse:
    """Close an open paper position at current market price."""
    try:
        position = await close_position(db, position_id=position_id, user_id=current_user.id)
        return _position_to_response(position)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@router.get("/positions", response_model=list[PositionResponse])
async def list_open_positions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PositionResponse]:
    """Return all currently open paper positions."""
    positions = await get_open_positions(db, user_id=current_user.id)
    return [_position_to_response(p) for p in positions]


@router.get("/positions/closed", response_model=list[PositionResponse])
async def list_closed_positions(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PositionResponse]:
    """Return closed paper positions, newest first."""
    positions = await get_closed_positions(db, user_id=current_user.id, limit=limit)
    return [_position_to_response(p) for p in positions]


@router.get("/portfolio", response_model=PortfolioSummaryResponse)
async def portfolio_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PortfolioSummaryResponse:
    """Return the user's paper-trading portfolio summary."""
    summary = await get_portfolio_summary(db, user_id=current_user.id)
    return PortfolioSummaryResponse(**summary)


@router.get("/trades", response_model=list[TradeResponse])
async def trade_history(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TradeResponse]:
    """Return paper trade history, newest first."""
    trades = await get_trade_history(db, user_id=current_user.id, limit=limit)
    return [_trade_to_response(t) for t in trades]


@router.post("/update", response_model=list[PositionResponse])
async def trigger_update(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PositionResponse]:
    """Trigger position price updates and stop-loss/take-profit checks."""
    try:
        positions = await update_positions(db, user_id=current_user.id)
        return [_position_to_response(p) for p in positions]
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Position update failed: {exc}",
        )
