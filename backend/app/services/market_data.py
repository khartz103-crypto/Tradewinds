"""Market data service — wraps providers with caching via MarketDataCache."""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.market_data_cache import MarketDataCache
from app.providers.alpaca import AlpacaProvider
from app.schemas.market_data import Bar, Quote, Snapshot

logger = logging.getLogger(__name__)

# ── singleton provider ───────────────────────────────────────────────

_provider: AlpacaProvider | None = None


def _get_provider() -> AlpacaProvider:
    """Lazy-create the singleton Alpaca provider."""
    global _provider
    if _provider is None:
        _provider = AlpacaProvider()
    return _provider


# ── cache helpers ────────────────────────────────────────────────────

_CACHE_PROVIDER = "alpaca"
_DATA_TYPE_BARS = "bars"
_DATA_TYPE_QUOTE = "quote"
_DATA_TYPE_SNAPSHOT = "snapshot"


async def _read_cache(
    session: AsyncSession,
    symbol: str,
    data_type: str,
) -> MarketDataCache | None:
    """Return a valid (non-expired) cache entry or None."""
    stmt = (
        select(MarketDataCache)
        .where(
            MarketDataCache.symbol == symbol.upper(),
            MarketDataCache.provider == _CACHE_PROVIDER,
            MarketDataCache.data_type == data_type,
        )
        .order_by(MarketDataCache.fetched_at.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    entry = result.scalar_one_or_none()
    if entry is None:
        return None
    now = datetime.now(timezone.utc)
    if entry.expires_at <= now:
        await session.delete(entry)
        await session.flush()
        return None
    return entry


async def _write_cache(
    session: AsyncSession,
    symbol: str,
    data_type: str,
    data: dict | list,
    ttl_minutes: int,
) -> MarketDataCache:
    """Write (or overwrite) a cache entry."""
    # Delete any existing entry for the same key to avoid duplicates
    stmt = select(MarketDataCache).where(
        MarketDataCache.symbol == symbol.upper(),
        MarketDataCache.provider == _CACHE_PROVIDER,
        MarketDataCache.data_type == data_type,
    )
    result = await session.execute(stmt)
    for old in result.scalars().all():
        await session.delete(old)
    await session.flush()

    now = datetime.now(timezone.utc)
    entry = MarketDataCache(
        symbol=symbol.upper(),
        provider=_CACHE_PROVIDER,
        data_type=data_type,
        data=data if isinstance(data, dict) else {"items": data},
        fetched_at=now,
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    session.add(entry)
    await session.flush()
    return entry


# ── public API ───────────────────────────────────────────────────────


async def get_daily_bars(
    session: AsyncSession,
    symbol: str,
    start_date: datetime,
    end_date: datetime,
) -> list[Bar]:
    """Fetch daily bars for *symbol* between *start_date* and *end_date*.

    Checks the ``MarketDataCache`` first; if no valid cached data is found
    the Alpaca provider is called and the result is cached.
    """
    cached = await _read_cache(session, symbol, _DATA_TYPE_BARS)
    if cached is not None:
        items: list = cached.data.get("items", cached.data) if isinstance(cached.data, dict) else cached.data
        # Only serve cache if the date range is covered (heuristic: at least
        # one bar in range).
        return [_bar_from_dict(b) for b in items]

    provider = _get_provider()
    raw = await provider.get_bars(symbol, start_date, end_date)

    ttl = settings.market_data_cache_ttl_minutes
    await _write_cache(session, symbol, _DATA_TYPE_BARS, raw, ttl)
    return [_bar_from_dict(b) for b in raw]


async def get_latest_quote(
    session: AsyncSession,
    symbol: str,
) -> Quote:
    """Fetch the latest quote for *symbol* (cached for 60 seconds)."""
    cached = await _read_cache(session, symbol, _DATA_TYPE_QUOTE)
    if cached is not None and isinstance(cached.data, dict):
        return Quote(**cached.data)

    provider = _get_provider()
    raw = await provider.get_quote(symbol)
    await _write_cache(session, symbol, _DATA_TYPE_QUOTE, raw, ttl_minutes=1)
    return Quote(**raw)


async def get_snapshots(
    session: AsyncSession,
    symbols: list[str],
) -> list[Snapshot]:
    """Batch-fetch snapshots for multiple symbols (cached per-symbol)."""
    results: list[Snapshot] = []
    uncached: list[str] = []

    for sym in symbols:
        cached = await _read_cache(session, sym, _DATA_TYPE_SNAPSHOT)
        if cached is not None and isinstance(cached.data, dict):
            results.append(Snapshot(**cached.data))
        else:
            uncached.append(sym)

    if uncached:
        provider = _get_provider()
        raw = await provider.get_snapshots(uncached)
        ttl = settings.market_data_cache_ttl_minutes
        for snap_dict in raw:
            sym = snap_dict["symbol"]
            await _write_cache(session, sym, _DATA_TYPE_SNAPSHOT, snap_dict, ttl)
            results.append(Snapshot(**snap_dict))

    # Return results in the original symbol order
    ordered = {s.symbol.upper(): s for s in results}
    return [ordered[s.upper()] for s in symbols if s.upper() in ordered]


# ── internal helpers ─────────────────────────────────────────────────


def _bar_from_dict(d: dict) -> Bar:
    """Convert a raw bar dict to a ``Bar`` Pydantic model."""
    return Bar(
        timestamp=_ensure_dt(d.get("timestamp")),
        open=d.get("open", 0),
        high=d.get("high", 0),
        low=d.get("low", 0),
        close=d.get("close", 0),
        volume=d.get("volume", 0),
    )


def _ensure_dt(val) -> datetime:
    """Coerce a value to a timezone-aware datetime."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, str):
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)
