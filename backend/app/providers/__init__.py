"""Market data provider plugin interface."""

from abc import ABC, abstractmethod
from datetime import datetime


class MarketDataProvider(ABC):
    """Abstract base class for market data providers (Alpaca, IBKR, Tradier, etc.)."""

    @abstractmethod
    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> list[dict]:
        """Fetch historical bars (OHLCV) for a symbol.

        Args:
            symbol: Stock/ETF symbol (e.g. "AAPL").
            start: Start datetime (inclusive).
            end: End datetime (inclusive).
            timeframe: Bar timeframe (default "1Day"). Common values:
                "1Min", "5Min", "15Min", "1Hour", "1Day".

        Returns:
            List of bar dicts with keys: timestamp, open, high, low, close, volume.
        """
        ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a symbol.

        Returns:
            Dict with keys: symbol, bid, ask, last, timestamp.
        """
        ...

    @abstractmethod
    async def get_snapshots(self, symbols: list[str]) -> list[dict]:
        """Fetch snapshots for multiple symbols (batch).

        Each snapshot includes the latest quote, latest daily bar,
        and daily change details.

        Returns:
            List of snapshot dicts.
        """
        ...
