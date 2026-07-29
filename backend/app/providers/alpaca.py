"""Alpaca Markets REST API client using httpx.

Implements the MarketDataProvider interface for Alpaca's market data endpoints:
- Historical bars: GET /v2/stocks/{symbol}/bars
- Latest quote:   GET /v2/stocks/{symbol}/quotes/latest
- Snapshots:       GET /v2/stocks/snapshots
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.providers import MarketDataProvider

logger = logging.getLogger(__name__)

# Alpaca rate limit: 200 req/min on free tier.
# We use a generous default timeout and respect rate-limit headers.
_RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"


class AlpacaProvider(MarketDataProvider):
    """Market data provider backed by Alpaca's REST API.

    Uses paper trading base URL by default. Set ALPACA_BASE_URL to switch
    to live trading.
    """

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        data_url: str | None = None,
    ) -> None:
        self.api_key = api_key or settings.alpaca_api_key_id
        self.secret_key = secret_key or settings.alpaca_api_secret_key
        self.base_url = (base_url or settings.alpaca_base_url).rstrip("/")
        self.data_url = (data_url or settings.alpaca_data_url).rstrip("/")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _auth_headers(self) -> dict[str, str]:
        """Return authentication headers for Alpaca API calls."""
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _has_credentials(self) -> bool:
        """Return True when both API key and secret are configured."""
        return bool(self.api_key and self.secret_key)

    def _check_credentials(self) -> None:
        """Raise a helpful error when credentials are missing."""
        if not self._has_credentials():
            raise RuntimeError(
                "Alpaca API credentials are not configured. "
                "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY "
                "environment variables to enable live market data. "
                "The provider can be instantiated without keys for testing, "
                "but API calls will fail."
            )

    async def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        """Perform an authenticated GET request and return JSON.

        Raises httpx.HTTPStatusError on non-2xx responses.
        """
        self._check_credentials()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=self._auth_headers, params=params)
            remaining = resp.headers.get(_RATE_LIMIT_REMAINING_HEADER, "?")
            logger.debug(
                "Alpaca GET %s → %d (rate-limit remaining: %s)",
                url,
                resp.status_code,
                remaining,
            )
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # MarketDataProvider implementation
    # ------------------------------------------------------------------

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Day",
    ) -> list[dict]:
        """Fetch historical bars from Alpaca.

        Endpoint: GET /v2/stocks/{symbol}/bars
        """
        url = f"{self.data_url}/v2/stocks/{symbol}/bars"
        params = {
            "start": _format_dt(start),
            "end": _format_dt(end),
            "timeframe": timeframe,
            "limit": 10000,
            "adjustment": "raw",
        }
        payload = await self._get(url, params=params)
        raw_bars: list[dict] = payload.get("bars", []) if isinstance(payload, dict) else []
        return [
            {
                "timestamp": _parse_dt(b["t"]),
                "open": _decimal(b.get("o", 0)),
                "high": _decimal(b.get("h", 0)),
                "low": _decimal(b.get("l", 0)),
                "close": _decimal(b.get("c", 0)),
                "volume": int(b.get("v", 0)),
            }
            for b in raw_bars
        ]

    async def get_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a single symbol.

        Endpoint: GET /v2/stocks/{symbol}/quotes/latest
        """
        url = f"{self.data_url}/v2/stocks/{symbol}/quotes/latest"
        payload = await self._get(url)
        q = payload.get("quote", payload)
        return {
            "symbol": symbol.upper(),
            "bid": _decimal(q.get("bp", 0)) if q.get("bp") else None,
            "ask": _decimal(q.get("ap", 0)) if q.get("ap") else None,
            "last": _decimal(q.get("ap", q.get("bp", 0))) if (q.get("ap") or q.get("bp")) else None,
            "timestamp": _parse_dt(q["t"]) if q.get("t") else datetime.now(timezone.utc),
        }

    async def get_snapshots(self, symbols: list[str]) -> list[dict]:
        """Fetch snapshots for multiple symbols in one request.

        Endpoint: GET /v2/stocks/snapshots?symbols=AAPL,MSFT
        """
        if not symbols:
            return []

        url = f"{self.data_url}/v2/stocks/snapshots"
        params = {"symbols": ",".join(s.upper() for s in symbols)}
        payload = await self._get(url, params=params)

        results: list[dict] = []
        for sym, snap in payload.items():
            quote_raw = snap.get("latestQuote", {}) or {}
            bar_raw = snap.get("latestTrade", {}) or {}
            daily_bar_raw = snap.get("dailyBar", {}) or {}
            prev_bar_raw = snap.get("prevDailyBar", {}) or {}

            daily_change_pct = None
            if daily_bar_raw and prev_bar_raw:
                prev_close = _decimal(prev_bar_raw.get("c", 0))
                curr_close = _decimal(daily_bar_raw.get("c", 0))
                if prev_close and prev_close > 0:
                    daily_change_pct = ((curr_close - prev_close) / prev_close) * 100

            results.append({
                "symbol": sym.upper(),
                "latest_quote": {
                    "symbol": sym.upper(),
                    "bid": _decimal(quote_raw.get("bp", 0)) if quote_raw.get("bp") else None,
                    "ask": _decimal(quote_raw.get("ap", 0)) if quote_raw.get("ap") else None,
                    "last": (
                        _decimal(bar_raw.get("p", 0))
                        if bar_raw.get("p")
                        else _decimal(daily_bar_raw.get("c", 0)) if daily_bar_raw.get("c") else None
                    ),
                    "timestamp": (
                        _parse_dt(quote_raw["t"])
                        if quote_raw.get("t")
                        else datetime.now(timezone.utc)
                    ),
                },
                "latest_bar": {
                    "timestamp": _parse_dt(daily_bar_raw["t"]) if daily_bar_raw.get("t") else datetime.now(timezone.utc),
                    "open": _decimal(daily_bar_raw.get("o", 0)),
                    "high": _decimal(daily_bar_raw.get("h", 0)),
                    "low": _decimal(daily_bar_raw.get("l", 0)),
                    "close": _decimal(daily_bar_raw.get("c", 0)),
                    "volume": int(daily_bar_raw.get("v", 0)),
                } if daily_bar_raw else None,
                "daily_change_pct": daily_change_pct,
            })
        return results


# ── helpers ──────────────────────────────────────────────────────────


def _format_dt(dt: datetime) -> str:
    """Format a datetime as ISO-8601 string for Alpaca API."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(val: str) -> datetime:
    """Parse an Alpaca timestamp string to a timezone-aware datetime."""
    dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _decimal(val: float | str | None) -> float | None:
    """Convert a numeric value to a float (compatible with Decimal)."""
    if val is None:
        return None
    return float(val)
