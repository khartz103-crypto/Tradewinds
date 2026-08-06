"""Tests for the momentum pullback strategy."""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.market_data import Bar
from app.services.strategy_engine import _min_bars_to_lookback_days
from app.strategies import STRATEGY_REGISTRY, BaseStrategy, get_strategy
from app.strategies.indicators import sma
from app.strategies.momentum_pullback import MomentumPullbackStrategy


def _bar(ts, o, h, l, c, v=1_000_000):
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)


def _line(start, end, n):
    """Linear ramp from start -> end across n bars (n >= 1)."""
    if n <= 1:
        return [end]
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def _to_bars(closes, start=datetime(2023, 1, 1, tzinfo=timezone.utc)):
    """Wrap a close series into OHLC bars (open = prev close, fixed range)."""
    bars = []
    prev_c = closes[0] - 0.1
    for i, c in enumerate(closes):
        o = prev_c
        h = max(o, c) + 0.4
        l = min(o, c) - 0.4
        bars.append(
            _bar(start + timedelta(days=i), round(o, 3), round(h, 3), round(l, 3), round(c, 3))
        )
        prev_c = c
    return bars


def _uptrend_bars(n_drift=260, n_rally=185, n_plateau=15, n_drop=30,
                  drop_pct=0.07, bounce=0.2, final_flush=True) -> list[Bar]:
    """Slow drift up → steep rally → peak plateau → pullback → bounce.

    The terminal shape satisfies every long condition:
    sma50 > sma200, price 3–10% below the recent high, close below the
    middle Bollinger Band (20-day SMA), ATR positive, close ticking up.
    """
    closes = []
    closes += _line(100.0, 105.0, n_drift)
    closes += _line(105.0, 132.0, n_rally)
    closes += _line(132.0, 132.6, n_plateau)
    peak = closes[-1]
    closes += _line(peak, peak * (1 - drop_pct), n_drop)
    if final_flush:
        closes[-1] = closes[-1] - 0.15
    closes.append(closes[-1] + bounce)
    return _to_bars(closes)


def _downtrend_bars(n=500) -> list[Bar]:
    """Steady decline ending near its low with a small up-tick.

    sma50 < sma200 (uptrend NOT established), price near the recent low
    (no short pullback zone either) → no signal in either direction.
    """
    closes = _line(100.0, 70.0, n)
    closes.append(closes[-1] + 0.1)
    return _to_bars(closes)


def _at_high_bars() -> list[Bar]:
    """Strong uptrend that holds AT the recent high — no pullback zone."""
    closes = []
    closes += _line(100.0, 105.0, 260)
    closes += _line(105.0, 132.0, 185)
    closes += [132.5] * 46  # flat at the peak: price ~0.3% below recent high
    return _to_bars(closes)


def _short_bars() -> list[Bar]:
    """Mirror image: downtrend → steep decline → low → rally → down tick."""
    closes = []
    closes += _line(132.0, 127.0, 260)
    closes += _line(127.0, 100.0, 185)
    closes += _line(100.0, 99.4, 15)
    low = closes[-1]
    closes += _line(low, low * (1 + 0.07), 30)
    closes[-1] = closes[-1] + 0.15  # spike up before the first down tick
    closes.append(closes[-1] - 0.2)
    return _to_bars(closes)


# ── registry tests ──────────────────────────────────────────────────────


def test_strategy_registered():
    assert "momentum_pullback" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["momentum_pullback"] is MomentumPullbackStrategy


def test_get_strategy():
    s = get_strategy("momentum_pullback", {"atr_stop_mult": 3.0})
    assert isinstance(s, MomentumPullbackStrategy)
    assert s.name == "momentum_pullback"
    assert s.config["atr_stop_mult"] == 3.0
    assert s._cfg("atr_stop_mult") == 3.0
    assert s._cfg("atr_target_mult") == 4.0  # default preserved


def test_min_bars_defaults():
    """BaseStrategy defaults to 120; momentum_pullback requires 400."""
    assert BaseStrategy.min_bars == 120
    assert MomentumPullbackStrategy.min_bars == 400
    assert MomentumPullbackStrategy().min_bars == 400


def test_min_bars_to_lookback_days():
    """400 daily bars needs ~2 years of calendar days; 120 needs < 180."""
    assert _min_bars_to_lookback_days(400) >= 550
    assert _min_bars_to_lookback_days(120) < 200
    assert _min_bars_to_lookback_days(400) > _min_bars_to_lookback_days(120)


# ── warmup ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_returns_none():
    """Fewer bars than min_bars (400) → None, regardless of shape."""
    s = MomentumPullbackStrategy()
    assert await s.analyze("TEST", _uptrend_bars()[:300]) is None
    assert await s.analyze("TEST", _uptrend_bars()[:399]) is None


@pytest.mark.asyncio
async def test_exactly_min_bars_analyzes():
    """At exactly min_bars the strategy runs (shape decides the signal)."""
    s = MomentumPullbackStrategy()
    bars = _uptrend_bars()[:400]
    result = await s.analyze("TEST", bars)
    assert result is None or result.symbol == "TEST"


# ── gate tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_signal_when_trend_not_established():
    """sma(50) < sma(200) → uptrend gate fails → no long signal."""
    s = MomentumPullbackStrategy()
    bars = _downtrend_bars()
    closes = [float(b.close) for b in bars]
    assert sma(closes, 50)[-1] < sma(closes, 200)[-1]  # trend really absent
    assert await s.analyze("TEST", bars) is None


@pytest.mark.asyncio
async def test_no_signal_when_price_at_high():
    """Price sitting at the recent high → not in the pullback zone → None."""
    s = MomentumPullbackStrategy()
    bars = _at_high_bars()
    result = await s.analyze("TEST", bars)
    assert result is None


@pytest.mark.asyncio
async def test_no_signal_when_pullback_too_shallow():
    """A 1.5% dip (below pullback_min_pct=3%) → None."""
    s = MomentumPullbackStrategy()
    bars = _uptrend_bars(drop_pct=0.015)
    assert await s.analyze("TEST", bars) is None


@pytest.mark.asyncio
async def test_no_signal_when_no_bounce():
    """Pullback still falling (no stabilization) → None."""
    s = MomentumPullbackStrategy()
    bars = _uptrend_bars(final_flush=True)
    # replace the final bounce with another down bar
    closes = [float(b.close) for b in bars]
    closes.append(closes[-1] - 0.2)
    result = await s.analyze("TEST", _to_bars(closes))
    assert result is None


# ── signal tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_when_all_conditions_fire():
    """All five long conditions met → a BUY signal with full structure."""
    s = MomentumPullbackStrategy()
    bars = _uptrend_bars()
    result = await s.analyze("AAPL", bars)
    assert result is not None
    assert result.symbol == "AAPL"
    assert result.action == "buy"
    assert result.entry_price == float(bars[-1].close)
    assert result.confidence == 100.0
    assert "All 5/5 conditions met" in result.reasoning
    assert "trend_established=PASS" in result.reasoning
    assert "stabilization=PASS" in result.reasoning
    # indicators fully populated
    for key in ("sma_fast", "sma_slow", "sma_mid", "bb_lower", "bb_upper",
                "atr", "recent_high", "recent_low", "pullback_pct",
                "latest_close", "prev_close", "conditions"):
        assert key in result.indicators, f"Missing indicator: {key}"
    assert result.indicators["conditions"]["trend_established"] is True


@pytest.mark.asyncio
async def test_stop_target_are_atr_multiples():
    """Long: stop = entry − 2×ATR, target = entry + 4×ATR (1:2 risk/reward)."""
    s = MomentumPullbackStrategy()
    result = await s.analyze("AAPL", _uptrend_bars())
    assert result is not None
    atr = result.indicators["atr"]
    # signal levels are rounded to 2dp — allow that rounding in the check
    assert result.stop_loss == pytest.approx(result.entry_price - 2.0 * atr, abs=1e-2)
    assert result.take_profit == pytest.approx(result.entry_price + 4.0 * atr, abs=1e-2)
    # proper 1:2 geometry (allow for independent rounding of SL and TP)
    risk = result.entry_price - result.stop_loss
    reward = result.take_profit - result.entry_price
    assert reward == pytest.approx(2.0 * risk, abs=0.03)


@pytest.mark.asyncio
async def test_short_side_mirror():
    """Mirrored logic: downtrend + rally off the low → SELL signal."""
    s = MomentumPullbackStrategy()
    result = await s.analyze("MSFT", _short_bars())
    assert result is not None
    assert result.action == "sell"
    atr = result.indicators["atr"]
    assert result.stop_loss == pytest.approx(result.entry_price + 2.0 * atr, abs=1e-2)
    assert result.take_profit == pytest.approx(result.entry_price - 4.0 * atr, abs=1e-2)
    assert result.indicators["conditions"]["trend_established"] is True
    # short pullback zone: 3–10% above the recent low
    rally_pct = result.indicators["rally_pct"]
    assert 0.03 <= rally_pct <= 0.10


@pytest.mark.asyncio
async def test_custom_config_respected():
    """Config overrides change the gates (e.g. tighter pullback zone)."""
    s = MomentumPullbackStrategy(config={
        "pullback_min_pct": 0.10,  # now the 7% pullback is too shallow
        "pullback_max_pct": 0.20,
    })
    assert await s.analyze("AAPL", _uptrend_bars()) is None


# ── end-to-end ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_trend_then_pullback():
    """Trending-then-pullback bars produce a tradeable buy signal.

    Verifies the exact entry, stop, and target derived from the bars, plus
    the reasoning text used by the scanner log and dashboard.
    """
    s = MomentumPullbackStrategy()
    bars = _uptrend_bars()
    result = await s.analyze("NVDA", bars)

    assert result is not None
    assert result.action == "buy"
    entry = result.entry_price
    assert entry == float(bars[-1].close)

    # geometry on the real ATR the strategy computed
    atr = result.indicators["atr"]
    assert result.stop_loss == pytest.approx(entry - 2 * atr, abs=1e-2)
    assert result.take_profit == pytest.approx(entry + 4 * atr, abs=1e-2)
    assert result.stop_loss < entry < result.take_profit

    # the pullback really is 3–10% below the recent high
    pullback = result.indicators["pullback_pct"]
    assert 0.03 <= pullback <= 0.10

    # reasoning is human-readable and mentions all key levels
    assert f"Entry: {entry:.2f}" in result.reasoning
    assert f"SL: {result.stop_loss:.2f}" in result.reasoning
    assert f"TP: {result.take_profit:.2f}" in result.reasoning
    assert "Momentum Pullback" in result.reasoning
