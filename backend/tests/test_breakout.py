"""Tests for the deliberately simple trend-breakout strategy."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas.market_data import Bar
from app.strategies import STRATEGY_REGISTRY, get_strategy
from app.strategies.breakout import BreakoutStrategy
from app.strategies.indicators import atr


def _bars(closes: list[float]) -> list[Bar]:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    previous = closes[0]
    result = []
    for i, close in enumerate(closes):
        result.append(Bar(
            timestamp=start + timedelta(days=i), open=Decimal(str(previous)),
            high=Decimal(str(max(previous, close) + 1)),
            low=Decimal(str(min(previous, close) - 1)),
            close=Decimal(str(close)), volume=1000,
        ))
        previous = close
    return result


def _uptrend(final: float = 140) -> list[Bar]:
    closes = [100 + i * 0.1 for i in range(300)]
    closes[-1] = final
    return _bars(closes)


def _downtrend(final: float = 60) -> list[Bar]:
    closes = [100 - i * 0.1 for i in range(300)]
    closes[-1] = final
    return _bars(closes)


@pytest.mark.asyncio
async def test_registered_and_warmup():
    assert STRATEGY_REGISTRY["breakout"] is BreakoutStrategy
    assert isinstance(get_strategy("breakout"), BreakoutStrategy)
    assert await BreakoutStrategy().analyze("TEST", _uptrend()[:299]) is None


@pytest.mark.asyncio
async def test_below_sma_has_no_long_signal():
    # Below the SMA, but not a fresh low: neither directional AND gate fires.
    closes = [100 + i * 0.1 for i in range(300)]
    closes[-2] = 89
    closes[-1] = 90
    assert await BreakoutStrategy().analyze("TEST", _bars(closes)) is None


@pytest.mark.asyncio
async def test_not_new_high_has_no_signal():
    closes = [100 + i * 0.1 for i in range(300)]
    closes[-2] = 140
    closes[-1] = 139
    assert await BreakoutStrategy().analyze("TEST", _bars(closes)) is None


@pytest.mark.asyncio
async def test_long_signal_and_atr_levels():
    bars = _uptrend()
    result = await BreakoutStrategy().analyze("AAPL", bars)
    assert result is not None
    assert result.action == "buy"
    assert result.confidence == 100
    assert result.reasoning == "New 20-day high above 200-SMA: breakout signal"
    expected_atr = [float(b.high) for b in bars]
    actual_atr = atr(expected_atr, [float(b.low) for b in bars], [float(b.close) for b in bars], 14)[-1]
    assert result.indicators["atr"] == pytest.approx(actual_atr)
    assert result.stop_loss == pytest.approx(result.entry_price - 2 * actual_atr, abs=0.01)
    assert result.take_profit == pytest.approx(result.entry_price + 4 * actual_atr, abs=0.01)


@pytest.mark.asyncio
async def test_short_breakout_is_mirrored():
    result = await BreakoutStrategy().analyze("MSFT", _downtrend())
    assert result is not None
    assert result.action == "sell"
    atr_value = result.indicators["atr"]
    assert result.stop_loss == pytest.approx(result.entry_price + 2 * atr_value, abs=0.01)
    assert result.take_profit == pytest.approx(result.entry_price - 4 * atr_value, abs=0.01)
    assert "New 20-day low" in result.reasoning


@pytest.mark.asyncio
async def test_end_to_end_rises_above_sma_then_breaks_out():
    closes = [100.0] * 210 + [100 + i * 0.5 for i in range(89)] + [160.0]
    result = await BreakoutStrategy().analyze("TSLA", _bars(closes))
    assert result is not None
    assert result.action == "buy"
    assert result.indicators["sma_200"] < result.entry_price
    assert result.indicators["recent_high_20"] < result.entry_price
