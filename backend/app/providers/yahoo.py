"""Yahoo Finance market data provider using the free yfinance client."""

import asyncio
from datetime import datetime

import yfinance as yf

from app.providers import MarketDataProvider


class YahooProvider(MarketDataProvider):
    """Market data provider backed by Yahoo Finance (no credentials required)."""

    def __init__(self) -> None:
        pass

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> list[dict]:
        """Fetch daily OHLCV bars for a symbol."""
        ticker = yf.Ticker(symbol)
        df = await asyncio.to_thread(
            ticker.history,
            start=start,
            end=end,
            interval="1d",
        )
        if df.empty:
            return []
        return [
            {
                "timestamp": index.to_pydatetime(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            }
            for index, row in df.iterrows()
        ]

    async def get_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a symbol."""
        ticker = yf.Ticker(symbol)
        info = await asyncio.to_thread(lambda: ticker.fast_info)
        return self._quote(symbol, info)

    async def get_snapshots(self, symbols: list[str]) -> list[dict]:
        """Fetch latest quote snapshots for each symbol."""
        results = []
        for sym in symbols:
            ticker = yf.Ticker(sym)
            info = await asyncio.to_thread(lambda: ticker.fast_info)
            quote = self._quote(sym, info)
            results.append(
                {
                    "symbol": sym.upper(),
                    "latest_quote": quote,
                    "latest_bar": None,
                    "daily_change_pct": None,
                }
            )
        return results

    @staticmethod
    def _quote(symbol: str, info) -> dict:
        return {
            "symbol": symbol.upper(),
            "bid": float(info.get("bid", 0) or 0),
            "ask": float(info.get("ask", 0) or 0),
            "last": float(info.get("lastPrice", 0) or 0),
            "timestamp": datetime.now(),
        }
