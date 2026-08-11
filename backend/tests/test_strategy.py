"""Tests for the trend-following strategy."""

from datetime import datetime, timezone

import pytest

from app.schemas.market_data import Bar
from app.strategies import STRATEGY_REGISTRY, get_strategy
from app.strategies.trend_following import TrendFollowingStrategy


def _make_bar(
    timestamp: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    v: int = 1000000,
) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=o,  # type: ignore[arg-type]
        high=h,  # type: ignore[arg-type]
        low=l,  # type: ignore[arg-type]
        close=c,  # type: ignore[arg-type]
        volume=v,  # type: ignore[arg-type]
    )


def _trending_bars(n: int = 120, bias: float = 0.3) -> list[Bar]:
    """Generate trending bars with an upward or downward bias."""
    import random
    random.seed(42)
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    for i in range(n):
        ts = base.replace(day=min(28, i + 1))
        change = bias + random.gauss(0, 0.8)
        o = price
        c = price + change
        bar_range = abs(change) + 0.3 + random.random() * 0.5
        h = max(o, c) + bar_range * 0.5
        l = min(o, c) - bar_range * 0.5
        v = int(1_000_000 + random.randint(-200_000, 600_000))
        bars.append(_make_bar(ts, o, h, l, c, v))
        price = c
    return bars


def _choppy_bars(n: int = 120) -> list[Bar]:
    """Generate oscillating (choppy) price action — no clear trend."""
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


# ── registry tests ──────────────────────────────────────────────────────


def test_strategy_registered():
    assert "trend_following" in STRATEGY_REGISTRY
    assert STRATEGY_REGISTRY["trend_following"] is TrendFollowingStrategy


def test_get_strategy():
    s = get_strategy("trend_following", {"adx_threshold": 20})
    assert isinstance(s, TrendFollowingStrategy)
    assert s.name == "trend_following"
    assert s.config["adx_threshold"] == 20


def test_get_strategy_missing():
    with pytest.raises(KeyError):
        get_strategy("nonexistent")


# ── analyze tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_too_few_bars():
    """Less than minimum bars → returns None."""
    s = TrendFollowingStrategy()
    bars = [
        _make_bar(datetime(2025, 1, d, tzinfo=timezone.utc), 100, 101, 99, 100.5)
        for d in range(1, 11)
    ]
    result = await s.analyze("AAPL", bars)
    assert result is None


@pytest.mark.asyncio
async def test_analyze_no_crash():
    """120 bars of trending data — does not crash."""
    s = TrendFollowingStrategy()
    bars = _trending_bars(120, bias=0.3)
    result = await s.analyze("TEST", bars)
    # May return None or a signal — both are valid outcomes.
    if result is not None:
        assert result.symbol == "TEST"
        assert result.action in ("buy", "sell", "hold")
        assert 0 <= result.confidence <= 100
        assert len(result.reasoning) > 0
        assert len(result.indicators) >= 10


@pytest.mark.asyncio
async def test_analyze_choppy_strict_returns_none():
    """Choppy price action with a strict min_signals=6 gate → no signal."""
    s = TrendFollowingStrategy(config={"min_signals": 6})
    result = await s.analyze("XYZ", _choppy_bars())
    assert result is None


@pytest.mark.asyncio
async def test_threshold_fires_on_partial_conditions():
    """Default 4/6 threshold: a majority of bearish conditions fires a SELL.

    The choppy fixture yields 5 bearish conditions (EMA/SMA/ADX/MACD/volume)
    even though ADX is below the trending threshold — exactly the case the
    all-or-nothing gate used to reject.
    """
    s = TrendFollowingStrategy()
    result = await s.analyze("XYZ", _choppy_bars())
    assert result is not None
    assert result.action == "sell"
    # 5 of 6 conditions met → confidence = 5/6 * 100, rounded to 1 decimal
    assert result.confidence == 83.3
    assert "(5/6 conditions met)" in result.reasoning
    assert "ema_alignment=PASS" in result.reasoning
    assert "rsi_zone=FAIL" in result.reasoning
    # Bearish levels: stop above entry, take-profit below entry
    assert result.entry_price is not None
    assert result.stop_loss is not None and result.stop_loss > result.entry_price
    assert result.take_profit is not None and result.take_profit < result.entry_price


@pytest.mark.asyncio
async def test_min_signals_config_lowers_threshold():
    """Lowering min_signals makes signals more likely; raising it to 6
    restores the old all-or-nothing gate (no signal on choppy data)."""
    assert TrendFollowingStrategy()._cfg("min_signals") == 3
    choppy = _choppy_bars()
    default = await TrendFollowingStrategy().analyze("XYZ", choppy)
    strict = await TrendFollowingStrategy(config={"min_signals": 6}).analyze("XYZ", choppy)
    assert default is not None
    assert strict is None


@pytest.mark.asyncio
async def test_analyze_downtrend_no_crash():
    """120 bars of downtrend — does not crash."""
    s = TrendFollowingStrategy()
    bars = _trending_bars(120, bias=-0.3)
    result = await s.analyze("TEST", bars)
    if result is not None:
        assert result.symbol == "TEST"
        assert result.action in ("buy", "sell", "hold")


@pytest.mark.asyncio
async def test_signal_structure_when_fired():
    """If a signal fires, verify its structure is correct."""
    s = TrendFollowingStrategy()
    # Wide-oscillation up-trend pattern: tends to satisfy more conditions
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    for i in range(120):
        ts = base.replace(day=min(28, i + 1))
        if i >= 115:
            c = price + 1.5
            h, l_v = c + 0.5, price - 0.2
            v = 5_000_000 if i == 119 else 1_500_000
        elif i % 2 == 0:
            c = price + 1.0
            h, l_v = c + 0.3, price - 0.1
            v = 1_200_000 + (i % 3) * 200_000
        else:
            c = price - 0.7
            h, l_v = price + 0.1, c - 0.3
            v = 1_200_000 + (i % 3) * 200_000
        bars.append(_make_bar(ts, price, h, l_v, c, v))
        price = c

    result = await s.analyze("TEST", bars)
    # The signal may or may not fire — the trend must still be strong enough
    # to satisfy the min_signals threshold.
    if result is not None:
        assert result.symbol == "TEST"
        assert result.action in ("buy", "sell")
        assert 0 < result.confidence <= 100
        assert result.entry_price is not None
        assert result.stop_loss is not None
        assert result.take_profit is not None
        assert len(result.reasoning) > 20
        # All expected indicator keys
        for key in ("ema_short", "ema_long", "sma_short", "sma_long",
                    "adx", "plus_di", "minus_di", "macd_line", "macd_signal",
                    "macd_histogram", "rsi", "atr", "volume_sma"):
            assert key in result.indicators, f"Missing indicator: {key}"


@pytest.mark.asyncio
async def test_custom_config():
    """Custom config values change indicator parameters."""
    s = TrendFollowingStrategy(config={
        "short_window": 10,
        "long_window": 30,
        "adx_threshold": 20,
        "volume_factor": 1.2,
    })
    assert s._cfg("short_window") == 10
    assert s._cfg("adx_threshold") == 20
    # Should still analyze without error
    bars = _trending_bars(120, bias=0.3)
    result = await s.analyze("CUSTOM", bars)
    # Not asserting signal — just that it doesn't crash and uses config
    assert result is None or result.symbol == "CUSTOM"


# ── plain-English summary tests ───────────────────────────────────────

TECHNICAL_TOKENS = (
    "ema_alignment", "sma_alignment", "adx_trending", "macd_momentum",
    "rsi_zone", "volume_confirmation", "PASS", "FAIL", "EMA", "SMA",
    "ADX", "MACD", "RSI", "/6", "conditions met",
)


@pytest.mark.asyncio
async def test_signal_summary_populated_plain_english():
    """A generated signal carries a non-empty plain-English summary.

    The choppy fixture deterministically fires a SELL (see
    test_threshold_fires_on_partial_conditions), so this exercises the
    summary built for a real signal, not just the helper.
    """
    s = TrendFollowingStrategy()
    result = await s.analyze("XYZ", _choppy_bars())
    assert result is not None
    assert result.summary, "summary must be populated on a generated signal"
    assert "XYZ" in result.summary
    # Plain-English phrasing, not a rehash of the reasoning string.
    assert "trend" in result.summary.lower() or "momentum" in result.summary.lower()
    assert "recommends selling" in result.summary
    for token in TECHNICAL_TOKENS:
        assert token not in result.summary, f"summary contains technical token: {token}"


def test_build_summary_buy_describes_only_passing_conditions():
    """BUY summary mentions passing conditions, never failed ones."""
    s = TrendFollowingStrategy()
    conditions = {
        "ema_alignment": True,
        "sma_alignment": True,
        "adx_trending": False,   # fails -> must not appear
        "macd_momentum": True,
        "rsi_zone": False,       # fails -> must not appear
        "volume_confirmation": False,  # fails -> must not appear
    }
    summary = s._build_summary("AAPL", "buy", conditions)
    assert "AAPL" in summary
    assert "clear uptrend" in summary
    assert "above its key moving averages" in summary
    assert "momentum is pushing prices in the same direction" in summary
    assert "recommends buying" in summary
    assert "strong" not in summary          # adx failed
    assert "volume" not in summary          # volume failed
    assert "room to run" not in summary     # rsi failed


def test_build_summary_sell_describes_only_passing_conditions():
    """SELL summary flips the pass direction: failed raw checks are the
    bearish ones, and failed-for-sell conditions are never mentioned.
    Phrasing stays truthful to what the raw check actually means (e.g.
    ADX below threshold -> no strength to reverse; volume not elevated ->
    buyers not stepping in)."""
    s = TrendFollowingStrategy()
    conditions = {
        "ema_alignment": False,        # below MAs -> passes for sell
        "sma_alignment": False,        # below MAs -> passes for sell
        "adx_trending": False,         # no trend strength -> passes for sell
        "macd_momentum": False,        # fading -> passes for sell
        "rsi_zone": True,              # raw True -> fails for sell, unmentioned
        "volume_confirmation": False,  # volume NOT elevated -> passes for sell
    }
    summary = s._build_summary("MSFT", "sell", conditions)
    assert "MSFT" in summary
    assert "trending down" in summary
    assert "below its key moving averages" in summary
    assert "momentum is fading" in summary
    assert "reverse course" in summary
    assert "buyers are not stepping in" in summary
    assert "recommends selling" in summary
    # Conditions that failed for sell are never described
    assert "room to run" not in summary   # rsi_zone failed for sell
    assert "overheated" not in summary
    assert "strong" not in summary        # no overclaiming from a weak ADX
    assert "volume is picking up" not in summary  # raw volume was NOT elevated


def test_build_summary_hold_returns_empty():
    """Non-trade actions produce no summary (defensive)."""
    s = TrendFollowingStrategy()
    conditions = {name: True for name in TECHNICAL_TOKENS[:6]}
    assert s._build_summary("AAPL", "hold", conditions) == ""
