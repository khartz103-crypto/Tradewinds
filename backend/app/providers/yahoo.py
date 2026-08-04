"""Yahoo Finance market data provider using the free yfinance client."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

import yfinance as yf

from app.providers import MarketDataProvider

logger = logging.getLogger(__name__)

# Yahoo applies limits per client/IP. Keep this shared across provider instances so
# concurrent scans cannot accidentally bypass the throttle.
_YAHOO_SEMAPHORE = asyncio.Semaphore(3)
_MAX_RETRIES = 3
_BACKOFF_SECONDS = 2.0


class YahooRateLimitError(RuntimeError):
    """Raised when Yahoo still rate-limits a request after all retries."""


class YahooProvider(MarketDataProvider):
    """Market data provider backed by Yahoo Finance (no credentials required)."""

    async def _request(self, operation: Callable[[], Any]) -> Any:
        """Run a blocking yfinance operation with shared throttling and retries."""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with _YAHOO_SEMAPHORE:
                    return await asyncio.to_thread(operation)
            except Exception as exc:
                message = str(exc)
                rate_limited = (
                    "too many requests" in message.lower()
                    or "rate limited" in message.lower()
                    or "http 429" in message.lower()
                    or "429" in message
                )
                if not rate_limited:
                    raise
                if attempt >= _MAX_RETRIES:
                    raise YahooRateLimitError(
                        "Yahoo Finance rate limit persisted after "
                        f"{_MAX_RETRIES} retries: {message}"
                    ) from exc
                delay = _BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "Yahoo Finance rate limit for request; retry %d/%d in %.1fs: %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                    message,
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> list[dict]:
        """Fetch daily OHLCV bars, preferring download's batched HTTP path."""
        interval = "1d" if timeframe == "1Day" else timeframe
        try:
            df = await self._request(
                lambda: yf.download(
                    symbol,
                    start=start,
                    end=end,
                    interval=interval,
                    progress=False,
                    auto_adjust=False,
                    threads=False,
                )
            )
        except YahooRateLimitError:
            raise
        except Exception:
            # Keep compatibility with yfinance versions/providers where download
            # is unavailable by falling back to the Ticker API.
            ticker = yf.Ticker(symbol)
            df = await self._request(
                lambda: ticker.history(start=start, end=end, interval=interval)
            )
        if df.empty:
            return []
        # yf.download returns MultiIndex columns even for one ticker in some
        # yfinance versions; normalize them before reading OHLCV fields.
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
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
        info = await self._request(lambda: ticker.fast_info)
        return self._quote(symbol, info)

    async def get_snapshots(self, symbols: list[str]) -> list[dict]:
        """Fetch latest quote snapshots, spacing requests to avoid rate limits."""
        results = []
        for index, sym in enumerate(symbols):
            if index:
                await asyncio.sleep(2.0)
            ticker = yf.Ticker(sym)
            info = await self._request(lambda: ticker.fast_info)
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
