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
async def test_analyze_choppy_returns_none():
    """Choppy price action (ADX near 0) → no signal."""
    s = TrendFollowingStrategy()
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    direction = 1.0
    for i in range(120):
        ts = base.replace(day=min(28, i + 1))
        o = price
        c = price + direction * 0.3
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        bars.append(_make_bar(ts, o, h, l, c, 1_000_000))
        price = c
        direction *= -1
    result = await s.analyze("XYZ", bars)
    assert result is None


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
    # The signal may or may not fire — the conditions are deliberately strict.
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
