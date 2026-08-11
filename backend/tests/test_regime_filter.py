"""Tests for the SPY>200-SMA regime filter in the strategy engine.

The gate lives in ``strategy_engine.run_strategy`` (shared by the scheduler
and the scanner API): when a strategy row has ``regime_filter="spy200sma"``,
signals are skipped unless SPY closes above its 200-day SMA.

Backtest evidence: OOS Sharpe 0.247 → 1.138 (+0.891), return 5.5×, PF 1.05 →
1.35, only ~17% of trades filtered. See /home/team/shared/regime_filter_spy200.md.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.strategy import Strategy
from app.schemas.market_data import Bar
from app.services.strategy_engine import REGIME_SMA_PERIOD, REGIME_SYMBOL, run_strategy


# ── helpers ──────────────────────────────────────────────────────────────


def _make_bar(ts: datetime, o: float, h: float, l: float, c: float, v: int = 1_000_000) -> Bar:
    return Bar(
        timestamp=ts,
        open=o,  # type: ignore[arg-type]
        high=h,  # type: ignore[arg-type]
        low=l,  # type: ignore[arg-type]
        close=c,  # type: ignore[arg-type]
        volume=v,
    )


def _choppy_bars(n: int = 120) -> list[Bar]:
    """Deterministic choppy price action — Trend Following fires a SELL on it
    with default config (5/6 bearish conditions)."""
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    direction = 1.0
    for i in range(n):
        ts = base.replace(day=min(28, i + 1))
        o = price
        c = price + direction * 0.3
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        bars.append(_make_bar(ts, o, h, l, c, 1_000_000))
        price = c
        direction *= -1
    return bars


def _spy_bars(tail_close: float, n: int = 260) -> list[Bar]:
    """SPY-like series: 240 flat bars at 100, then 20 bars at *tail_close*.

    With n=260 the 200-SMA of the last bar is
    (180 * 100 + 20 * tail_close) / 200:
      - tail_close=80  → SMA 98.0, close 80  <  SMA → filtered
      - tail_close=120 → SMA 102.0, close 120 >  SMA → passes
    """
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(n):
        ts = base + timedelta(days=i)
        close = 100.0 if i < n - 20 else float(tail_close)
        bars.append(_make_bar(ts, close, close + 0.2, close - 0.2, close))
    return bars


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Minimal AsyncSession stand-in: run_strategy only executes the strategy
    SELECT on this session (bars come from the monkeypatched get_daily_bars)."""

    def __init__(self, strategy: Strategy):
        self._strategy = strategy

    async def execute(self, _stmt):
        return _FakeResult(self._strategy)


def _make_db_strategy(regime_filter: str | None) -> Strategy:
    return Strategy(
        name="trend_following",
        display_name="Trend Following",
        description="",
        is_enabled=True,
        config={},
        regime_filter=regime_filter,
    )


async def _run(monkeypatch, spy_bars, regime_filter: str | None) -> list:
    """Run run_strategy('trend_following', ['TEST']) with the gate enabled or
    disabled, returning the surviving signals."""
    db = _FakeSession(_make_db_strategy(regime_filter))

    async def fake_get_daily_bars(_db, symbol, _start, _end):
        if symbol == REGIME_SYMBOL:
            return spy_bars
        return _choppy_bars()

    monkeypatch.setattr("app.services.strategy_engine.get_daily_bars", fake_get_daily_bars)
    return await run_strategy(db, "trend_following", ["TEST"])


# ── model ────────────────────────────────────────────────────────────────


def test_strategy_model_regime_filter_defaults_to_none():
    assert Strategy(name="x", display_name="X").regime_filter is None


def test_strategy_model_regime_filter_loads_config():
    strategy = Strategy(name="x", display_name="X", regime_filter="spy200sma")
    assert strategy.regime_filter == "spy200sma"


# ── engine gate ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_filters_signal_when_spy_below_sma200(monkeypatch):
    signals = await _run(monkeypatch, _spy_bars(tail_close=80.0), "spy200sma")
    assert signals == []


@pytest.mark.asyncio
async def test_gate_passes_signal_when_spy_above_sma200(monkeypatch):
    signals = await _run(monkeypatch, _spy_bars(tail_close=120.0), "spy200sma")
    assert len(signals) == 1
    assert signals[0].symbol == "TEST"
    assert signals[0].action in ("buy", "sell")


@pytest.mark.asyncio
async def test_no_gate_when_regime_filter_disabled(monkeypatch):
    # SPY below its SMA200, but regime_filter is None → signal passes.
    signals = await _run(monkeypatch, _spy_bars(tail_close=80.0), None)
    assert len(signals) == 1
    assert signals[0].symbol == "TEST"


@pytest.mark.asyncio
async def test_gate_fail_safe_skips_when_spy_data_unavailable(monkeypatch):
    # No SPY bars → cannot evaluate the gate → signals are skipped (fail-safe).
    signals = await _run(monkeypatch, [], "spy200sma")
    assert signals == []


@pytest.mark.asyncio
async def test_gate_fail_safe_skips_when_spy_history_too_short(monkeypatch):
    # Fewer than REGIME_SMA_PERIOD bars → SMA undefined → signals skipped.
    signals = await _run(monkeypatch, _spy_bars(80.0, n=REGIME_SMA_PERIOD - 1), "spy200sma")
    assert signals == []


@pytest.mark.asyncio
async def test_gate_passes_all_symbols_when_spy_above_sma200(monkeypatch):
    """SPY above the gate → every symbol's signal survives."""
    db = _FakeSession(_make_db_strategy("spy200sma"))

    async def fake_get_daily_bars(_db, symbol, _start, _end):
        if symbol == REGIME_SYMBOL:
            return _spy_bars(tail_close=120.0)  # SPY above SMA200 → pass
        return _choppy_bars()

    monkeypatch.setattr("app.services.strategy_engine.get_daily_bars", fake_get_daily_bars)
    signals = await run_strategy(db, "trend_following", ["TEST", "AAPL"])
    assert len(signals) == 2


@pytest.mark.asyncio
async def test_spy_fetch_uses_extended_lookback(monkeypatch):
    """The SPY fetch must request enough history for a defined 200-SMA
    (the strategy's own lookback of ~120 bars would not suffice)."""
    spy_starts: list[datetime] = []

    async def fake_get_daily_bars(_db, symbol, start, _end):
        if symbol == REGIME_SYMBOL:
            spy_starts.append(start)
            return _spy_bars(tail_close=120.0)
        return _choppy_bars()

    monkeypatch.setattr("app.services.strategy_engine.get_daily_bars", fake_get_daily_bars)
    await run_strategy(
        _FakeSession(_make_db_strategy("spy200sma")), "trend_following", ["TEST"]
    )
    assert len(spy_starts) == 1
    # ~319 calendar days requested (200 bars * 365/252 * 1.1 buffer).
    assert spy_starts[0] < datetime.now(timezone.utc) - timedelta(days=300)
