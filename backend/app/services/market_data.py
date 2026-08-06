"""Market data service — wraps providers with caching via MarketDataCache."""

import json
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.market_data_cache import MarketDataCache
from app.providers.yahoo import YahooProvider
from app.schemas.market_data import Bar, Quote, Snapshot

logger = logging.getLogger(__name__)


def _json_safe(obj):
    """Recursively convert datetime/date objects to ISO strings for JSON storage."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj

# ── singleton provider ───────────────────────────────────────────────

_provider: YahooProvider | None = None


def _get_provider() -> YahooProvider:
    """Lazy-create the singleton Yahoo Finance provider."""
    global _provider
    if _provider is None:
        _provider = YahooProvider()
    return _provider


# ── cache helpers ────────────────────────────────────────────────────

_CACHE_PROVIDER = "yahoo"
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
        data=_json_safe(data if isinstance(data, dict) else {"items": data}),
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

    Checks the ``MarketDataCache`` first. Cached bars are served only when
    they cover the requested range (within a tolerance for the most recent
    bar, which is often not printed yet); otherwise the Yahoo Finance provider
    is called again and the cache entry is overwritten. The returned list is
    always filtered to ``[start_date, end_date]``.
    """
    cached = await _read_cache(session, symbol, _DATA_TYPE_BARS)
    if cached is not None:
        items: list = cached.data.get("items", cached.data) if isinstance(cached.data, dict) else cached.data
        bars = sorted((_bar_from_dict(b) for b in items), key=lambda b: b.timestamp)
        if _covers_range(bars, start_date, end_date):
            return _filter_bars(bars, start_date, end_date)

    provider = _get_provider()
    raw = await provider.get_bars(symbol, start_date, end_date)

    ttl = settings.market_data_cache_ttl_minutes
    await _write_cache(session, symbol, _DATA_TYPE_BARS, raw, ttl)
    bars = [_bar_from_dict(b) for b in raw]
    return _filter_bars(bars, start_date, end_date)


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


def _filter_bars(bars: list[Bar], start_date, end_date) -> list[Bar]:
    """Return only bars whose timestamps fall within ``[start_date, end_date]``."""
    start = _ensure_dt(start_date)
    end = _ensure_dt(end_date)
    return [b for b in bars if start <= _ensure_dt(b.timestamp) <= end]


def _covers_range(bars: list[Bar], start_date, end_date, tolerance_days: int = 3) -> bool:
    """Heuristic coverage check: can cached *bars* (ascending) serve the request?

    Returns ``True`` when the cached series starts at or before *start_date*
    and ends at or after *end_date* minus a small tolerance. The tolerance
    exists because the most recent daily bar is often not printed yet (weekend,
    pre-market, or today's session still in progress); without it the scanner
    would refetch and overwrite the cache on every cycle.
    """
    if not bars:
        return False
    start = _ensure_dt(start_date)
    end = _ensure_dt(end_date)
    first = _ensure_dt(bars[0].timestamp)
    last = _ensure_dt(bars[-1].timestamp)
    if first > start:
        return False
    if last < end - timedelta(days=tolerance_days):
        return False
    return True


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
    if isinstance(val, date):
        return datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
    if isinstance(val, str):
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)
