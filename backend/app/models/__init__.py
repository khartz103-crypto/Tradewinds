"""TradeWind AI — SQLAlchemy models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all models."""


# Import models so they register with Base.metadata
from app.models.user import User  # noqa: E402, F401
from app.models.strategy import Strategy  # noqa: E402, F401
from app.models.watchlist import Watchlist  # noqa: E402, F401
from app.models.position import Position  # noqa: E402, F401
from app.models.trade import Trade  # noqa: E402, F401
from app.models.market_data_cache import MarketDataCache  # noqa: E402, F401
from app.models.ai_scan_result import AIScanResult  # noqa: E402, F401
from app.models.risk_settings import RiskSettings  # noqa: E402, F401

__all__ = [
    "Base",
    "User",
    "Strategy",
    "Watchlist",
    "Position",
    "Trade",
    "MarketDataCache",
    "AIScanResult",
    "RiskSettings",
]
