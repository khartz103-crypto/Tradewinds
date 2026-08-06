"""Tests for the market-data cache range filtering fix.

``get_daily_bars`` must filter cached bars to the requested range and must
refetch + overwrite the cache when the cached series does not cover it.
All tests run without a database (cache/provider helpers are patched).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.market_data import Bar
from app.services import market_data


def _day(offset: int) -> datetime:
    """Jan 1 2025 + *offset* days (UTC)."""
    return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=offset)


def _bar_dict(offset: int, price: float = 100.0) -> dict:
    return {
        "timestamp": _day(offset).isoformat(),
        "open": price,
        "high": price + 1.0,
        "low": price - 1.0,
        "close": price + 0.5,
        "volume": 1_000_000,
    }


class _FakeCacheEntry:
    def __init__(self, items: list[dict]):
        self.data = {"items": items}


class _FakeProvider:
    def __init__(self, bars: list[dict]):
        self.bars = bars
        self.calls = 0

    async def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[dict]:
        self.calls += 1
        return self.bars


async def _fake_read(cached):
    async def _read(session, symbol, data_type):
        return cached
    return _read


@pytest.mark.asyncio
async def test_cache_served_and_filtered_to_range(monkeypatch):
    """Wide cached window → cached bars are served, clipped to [start, end]."""
    cached = _FakeCacheEntry([_bar_dict(i) for i in range(0, 30)])
    monkeypatch.setattr(market_data, "_read_cache", await _fake_read(cached))
    provider = _FakeProvider([])
    monkeypatch.setattr(market_data, "_get_provider", lambda: provider)

    bars: list[Bar] = await market_data.get_daily_bars(
        object(), "AAPL", _day(5), _day(10)
    )

    assert len(bars) == 6  # Jan 6..Jan 11
    assert all(_day(5) <= b.timestamp <= _day(10) for b in bars)
    assert provider.calls == 0  # never hit the provider


@pytest.mark.asyncio
async def test_cache_missing_range_refetches_and_overwrites(monkeypatch):
    """Cache too old for the request → provider is called and cache overwritten."""
    cached = _FakeCacheEntry([_bar_dict(i) for i in range(0, 30)])  # Jan 1..Jan 30
    monkeypatch.setattr(market_data, "_read_cache", await _fake_read(cached))

    written: list[list[dict]] = []

    async def fake_write(session, symbol, data_type, data, ttl_minutes):
        written.append(list(data))

    monkeypatch.setattr(market_data, "_write_cache", fake_write)
    provider = _FakeProvider([_bar_dict(i) for i in range(60, 91)])  # Mar 1..Mar 31
    monkeypatch.setattr(market_data, "_get_provider", lambda: provider)

    bars: list[Bar] = await market_data.get_daily_bars(
        object(), "AAPL", _day(60), _day(90)
    )

    assert provider.calls == 1
    assert len(written) == 1
    assert len(written[0]) == 31  # the new cache entry holds the fresh data
    assert len(bars) == 31
    assert all(_day(60) <= b.timestamp <= _day(90) for b in bars)


@pytest.mark.asyncio
async def test_cache_tolerance_for_recent_bar(monkeypatch):
    """Cache ending a couple of days before end_date is still served.

    The most recent daily bar is frequently not printed yet (weekend /
    pre-market), so a small tolerance avoids refetching every cycle.
    """
    cached = _FakeCacheEntry([_bar_dict(i) for i in range(0, 10)])  # ends Jan 10
    monkeypatch.setattr(market_data, "_read_cache", await _fake_read(cached))
    provider = _FakeProvider([])
    monkeypatch.setattr(market_data, "_get_provider", lambda: provider)

    bars = await market_data.get_daily_bars(object(), "AAPL", _day(0), _day(12))

    assert provider.calls == 0
    assert len(bars) == 10


@pytest.mark.asyncio
async def test_cache_starting_after_requested_start_refetches(monkeypatch):
    """Cache beginning after start_date is incomplete → refetch."""
    cached = _FakeCacheEntry([_bar_dict(i) for i in range(10, 40)])  # Jan 11..Feb 9
    monkeypatch.setattr(market_data, "_read_cache", await _fake_read(cached))

    written: list[list[dict]] = []

    async def fake_write(session, symbol, data_type, data, ttl_minutes):
        written.append(list(data))

    monkeypatch.setattr(market_data, "_write_cache", fake_write)
    provider = _FakeProvider([_bar_dict(i) for i in range(0, 30)])
    monkeypatch.setattr(market_data, "_get_provider", lambda: provider)

    bars = await market_data.get_daily_bars(object(), "AAPL", _day(0), _day(29))

    assert provider.calls == 1
    assert len(written) == 1
    assert len(bars) == 30
