"""Dashboard routes — live performance metrics for the profit dashboard."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.paper_trading import DashboardPerformanceResponse
from app.services.dashboard import get_dashboard_performance

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/performance", response_model=DashboardPerformanceResponse)
async def dashboard_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardPerformanceResponse:
    """Return live paper-trading performance metrics.

    Includes summary stats (return, P&L, win rate, profit factor, Sharpe,
    max drawdown, avg holding days), a per-symbol breakdown, the reconstructed
    equity curve, and the most recent closed positions.
    """
    return await get_dashboard_performance(db, current_user.id)
