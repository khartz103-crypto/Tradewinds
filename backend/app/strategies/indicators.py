"""Pure-Python technical indicator calculations.

All functions accept plain ``list[float]`` and return matching-length lists
(padded with ``None`` at the start where there is insufficient data).
"""

from __future__ import annotations

import math


# ── helpers ─────────────────────────────────────────────────────────────

def _pad(length: int, values: list[float]) -> list[float | None]:
    """Left-pad *values* with None so the result has *length* entries."""
    missing = length - len(values)
    if missing <= 0:
        return list(values)
    return [None] * missing + list(values)


# ── Simple Moving Average ───────────────────────────────────────────────

def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average over *period* bars.

    Returns a list the same length as *values*.  Entries where there are
    fewer than *period* data points are ``None``.
    """
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * (period - 1)
    window_sum = sum(values[:period])
    result.append(window_sum / period)

    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result.append(window_sum / period)

    return result


# ── Exponential Moving Average ──────────────────────────────────────────

def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average (EMA) with Wilder's smoothing.

    Returns a list the same length as *values*.
    """
    if len(values) < period:
        return [None] * len(values)

    multiplier = 2.0 / (period + 1)
    result: list[float | None] = [None] * (period - 1)

    # seed with SMA of the first *period* values
    seed = sum(values[:period]) / period
    result.append(seed)

    for i in range(period, len(values)):
        ema_val = (values[i] - result[-1]) * multiplier + result[-1]
        result.append(ema_val)

    return result


# ── MACD ────────────────────────────────────────────────────────────────

def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD (Moving Average Convergence Divergence).

    Returns ``(macd_line, signal_line, histogram)`` — three equal-length
    lists.
    """
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)

    # macd_line = ema_fast - ema_slow
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is None or s is None:
            macd_line.append(None)
        else:
            macd_line.append(f - s)

    # signal_line = ema(macd_line, signal)
    # extract only the non-None portion for the EMA calc
    macd_clean = [v for v in macd_line if v is not None]
    if len(macd_clean) < signal:
        sig_clean: list[float | None] = [None] * len(macd_line)
    else:
        none_count = next(i for i, v in enumerate(macd_line) if v is not None)
        sig_raw = [v for v in ema(macd_clean, signal) if v is not None]
        sig_clean = [None] * none_count + sig_raw

    # pad signal to match macd_line length
    if len(sig_clean) < len(macd_line):
        sig_clean = [None] * (len(macd_line) - len(sig_clean)) + sig_clean

    # histogram = macd_line - signal_line
    histogram: list[float | None] = []
    for m, s in zip(macd_line, sig_clean):
        if m is not None and s is not None:
            histogram.append(m - s)
        else:
            histogram.append(None)

    return macd_line, sig_clean, histogram


# ── RSI ─────────────────────────────────────────────────────────────────

def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index (Wilder's smoothing).

    Returns a list the same length as *values*.
    """
    if len(values) < period + 1:
        return [None] * len(values)

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    result: list[float | None] = [None] * period

    # initial average gain / loss (simple average of first *period* deltas)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        rs = g / l
        return 100.0 - (100.0 / (1.0 + rs))

    result.append(_rsi(avg_gain, avg_loss))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result.append(_rsi(avg_gain, avg_loss))

    return result


# ── ADX ─────────────────────────────────────────────────────────────────

def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Average Directional Index with +DI and -DI.

    Returns ``(adx_line, plus_di, minus_di)`` — three equal-length lists.
    """
    n = len(highs)
    if n < period + 1:
        none_list: list[float | None] = [None] * n
        return none_list, none_list, none_list

    # True Range
    tr_list: list[float] = []
    for i in range(n):
        if i == 0:
            tr_list.append(highs[0] - lows[0])
        else:
            tr_list.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

    # Directional Movement
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(n):
        if i == 0:
            plus_dm.append(0.0)
            minus_dm.append(0.0)
        else:
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            if up > down and up > 0:
                plus_dm.append(up)
            else:
                plus_dm.append(0.0)
            if down > up and down > 0:
                minus_dm.append(down)
            else:
                minus_dm.append(0.0)

    # Smoothed True Range (Wilder's smoothing: first value is simple sum)
    atr_smooth: list[float | None] = [None] * (period - 1)
    atr_smooth.append(sum(tr_list[:period]))

    for i in range(period, n):
        prev = atr_smooth[-1]
        if prev is not None:
            atr_smooth.append((prev * (period - 1) + tr_list[i]) / period)
        else:
            atr_smooth.append(None)

    # Smoothed +DM and -DM
    plus_dm_smooth: list[float | None] = [None] * (period - 1)
    plus_dm_smooth.append(sum(plus_dm[:period]))
    minus_dm_smooth: list[float | None] = [None] * (period - 1)
    minus_dm_smooth.append(sum(minus_dm[:period]))

    for i in range(period, n):
        prev_p = plus_dm_smooth[-1]
        prev_m = minus_dm_smooth[-1]
        if prev_p is not None:
            plus_dm_smooth.append((prev_p * (period - 1) + plus_dm[i]) / period)
        else:
            plus_dm_smooth.append(None)
        if prev_m is not None:
            minus_dm_smooth.append((prev_m * (period - 1) + minus_dm[i]) / period)
        else:
            minus_dm_smooth.append(None)

    # +DI and -DI
    plus_di: list[float | None] = []
    minus_di: list[float | None] = []
    for p, m, tr in zip(plus_dm_smooth, minus_dm_smooth, atr_smooth):
        if tr is not None and tr != 0:
            if p is not None:
                plus_di.append(100.0 * p / tr)
            else:
                plus_di.append(None)
            if m is not None:
                minus_di.append(100.0 * m / tr)
            else:
                minus_di.append(None)
        else:
            plus_di.append(None)
            minus_di.append(None)

    # DX and ADX
    dx_list: list[float | None] = []
    for p, m in zip(plus_di, minus_di):
        if p is not None and m is not None:
            denom = p + m
            if denom == 0:
                dx_list.append(0.0)
            else:
                dx_list.append(100.0 * abs(p - m) / denom)
        else:
            dx_list.append(None)

    # ADX = EMA of DX (Wilder's smoothing, same period)
    # Find first index where DX is non-None
    first_dx_idx: int | None = None
    for i, v in enumerate(dx_list):
        if v is not None:
            first_dx_idx = i
            break

    if first_dx_idx is None or (n - first_dx_idx) < period:
        adx_line: list[float | None] = [None] * n
    else:
        # fdx_clean[i] corresponds to dx_list[first_dx_idx + i]
        fdx_clean = [dx_list[first_dx_idx + i] for i in range(n - first_dx_idx)]  # type: ignore[arg-type]
        # first ADX is simple average of first *period* DX values at
        # index first_dx_idx + period - 1
        adx_start_idx = first_dx_idx + period - 1
        adx_raw: list[float | None] = [sum(fdx_clean[:period]) / period]
        for i in range(period, len(fdx_clean)):
            prev = adx_raw[-1]
            if prev is not None:
                adx_raw.append((prev * (period - 1) + fdx_clean[i]) / period)  # type: ignore[operator]
            else:
                adx_raw.append(None)
        adx_line = [None] * adx_start_idx + adx_raw
        if len(adx_line) < n:
            adx_line += [None] * (n - len(adx_line))

    return adx_line, plus_di, minus_di


# ── ATR ─────────────────────────────────────────────────────────────────

def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float | None]:
    """Average True Range (Wilder's smoothing).

    Returns a list the same length as *highs*.
    """
    n = len(highs)
    if n < period + 1:
        return [None] * n

    # True Range
    tr_list: list[float] = []
    for i in range(n):
        if i == 0:
            tr_list.append(highs[0] - lows[0])
        else:
            tr_list.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )

    result: list[float | None] = [None] * period
    # first ATR is simple average of first *period* TRs
    result.append(sum(tr_list[:period]) / period)

    for i in range(period, n):
        prev = result[-1]
        if prev is not None:
            result.append((prev * (period - 1) + tr_list[i]) / period)
        else:
            result.append(None)

    return result


# ── Volume SMA ──────────────────────────────────────────────────────────

def volume_sma(volumes: list[int | float], period: int) -> list[float | None]:
    """Simple moving average of volume values."""
    values = [float(v) for v in volumes]
    return sma(values, period)
