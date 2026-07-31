"""Market data routes — bars, quotes, snapshots."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.market_data import Bar, Quote, Snapshot
from app.services.market_data import get_daily_bars, get_latest_quote, get_snapshots

router = APIRouter(prefix="/api/market", tags=["market_data"])


class SnapshotsRequest(BaseModel):
    """Request body for batch snapshot fetch."""

    symbols: list[str]


@router.get("/bars/{symbol}", response_model=list[Bar])
async def bars(
    symbol: str,
    start_date: datetime = Query(..., description="Start date (ISO format)"),
    end_date: datetime = Query(..., description="End date (ISO format)"),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Bar]:
    """Fetch daily OHLCV bars for *symbol* between *start_date* and *end_date*."""
    try:
        return await get_daily_bars(db, symbol, start_date, end_date)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch bars for {symbol}: {exc}",
        )


@router.get("/quotes/{symbol}", response_model=Quote)
async def quote(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Quote:
    """Fetch the latest quote (bid/ask/last) for *symbol*."""
    try:
        return await get_latest_quote(db, symbol)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch quote for {symbol}: {exc}",
        )


@router.post("/snapshots", response_model=list[Snapshot])
async def snapshots(
    body: SnapshotsRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Snapshot]:
    """Batch-fetch snapshots (quote + daily bar + change) for multiple symbols."""
    if not body.symbols:
        return []
    try:
        return await get_snapshots(db, body.symbols)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch snapshots: {exc}",
        )
