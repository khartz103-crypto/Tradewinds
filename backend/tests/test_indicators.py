"""Tests for pure-Python indicator calculations."""

import pytest
from app.strategies.indicators import adx, atr, ema, macd, rsi, sma, volume_sma


# ── SMA ─────────────────────────────────────────────────────────────────

def test_sma_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    result = sma(values, 3)
    assert result == [None, None, 2.0, 3.0, 4.0, 5.0]


def test_sma_too_short():
    assert sma([1.0, 2.0], 5) == [None, None]


def test_sma_exact_length():
    result = sma([1.0, 2.0, 3.0], 3)
    assert result == [None, None, 2.0]


# ── EMA ─────────────────────────────────────────────────────────────────

def test_ema_basic():
    values = [22.0, 23.0, 24.0, 25.0, 26.0, 27.0]
    result = ema(values, 3)
    assert result[:3] == [None, None, 23.0]  # seed = avg of first 3
    # Next: multiplier = 2/(3+1) = 0.5; ema = (25 - 23)*0.5 + 23 = 24.0
    assert result[3] == pytest.approx(24.0)
    # ema = (26 - 24)*0.5 + 24 = 25.0
    assert result[4] == pytest.approx(25.0)
    # ema = (27 - 25)*0.5 + 25 = 26.0
    assert result[5] == pytest.approx(26.0)


def test_ema_too_short():
    assert ema([1.0], 5) == [None]


# ── MACD ────────────────────────────────────────────────────────────────

def test_macd_length():
    """MACD should return three equal-length lists."""
    values = list(range(1, 101))  # 1..100
    m, s, h = macd(values, fast=12, slow=26, signal=9)
    assert len(m) == len(values)
    assert len(s) == len(values)
    assert len(h) == len(values)


def test_macd_known():
    """Smoke-test MACD with a known scenario — values should be finite."""
    values = [float(i) for i in range(1, 51)]
    m, s, h = macd(values, fast=12, slow=26, signal=9)
    # The last few values should all be non-None
    assert m[-1] is not None
    assert s[-1] is not None
    assert h[-1] is not None


# ── RSI ─────────────────────────────────────────────────────────────────

def test_rsi_all_up():
    """If all moves are up, RSI should be 100."""
    values = [float(i) for i in range(1, 30)]
    result = rsi(values, 14)
    assert result[-1] is not None
    assert result[-1] == pytest.approx(100.0)


def test_rsi_all_down():
    """If all moves are down, RSI should be 0."""
    values = [float(30 - i) for i in range(30)]
    result = rsi(values, 14)
    assert result[-1] is not None
    assert result[-1] == pytest.approx(0.0)


def test_rsi_too_short():
    assert rsi([1.0, 2.0], 14) == [None, None]


# ── ADX ─────────────────────────────────────────────────────────────────

def test_adx_flat():
    """Flat prices → ADX should be near zero."""
    n = 30
    h = [10.0] * n
    l = [9.0] * n
    c = [9.5] * n
    adx_line, plus_di, minus_di = adx(h, l, c, 14)
    assert adx_line[-1] is not None
    assert adx_line[-1] == pytest.approx(0.0, abs=1.0)


def test_adx_trending():
    """Steady uptrend should give positive ADX with +DI > -DI."""
    n = 30
    h = [10.0 + i * 0.2 for i in range(n)]
    l = [9.5 + i * 0.2 for i in range(n)]
    c = [9.8 + i * 0.2 for i in range(n)]
    adx_line, plus_di, minus_di = adx(h, l, c, 14)
    assert adx_line[-1] is not None
    assert plus_di[-1] is not None
    assert minus_di[-1] is not None
    # trending market: +DI > -DI
    assert plus_di[-1] > minus_di[-1]


def test_adx_too_short():
    n = 10
    h = [10.0] * n
    l = [9.0] * n
    c = [9.5] * n
    adx_line, plus_di, minus_di = adx(h, l, c, 14)
    assert all(v is None for v in adx_line)


# ── ATR ─────────────────────────────────────────────────────────────────

def test_atr_constant():
    n = 20
    h = [10.0] * n
    l = [9.0] * n
    c = [9.5] * n
    result = atr(h, l, c, 14)
    assert result[-1] == pytest.approx(1.0)


def test_atr_too_short():
    result = atr([10.0, 11.0], [9.0, 10.0], [9.5, 10.5], 14)
    assert result == [None, None]


# ── Volume SMA ──────────────────────────────────────────────────────────

def test_volume_sma():
    volumes = [100, 200, 300, 400, 500]
    result = volume_sma(volumes, 3)
    assert result == [None, None, 200.0, 300.0, 400.0]
