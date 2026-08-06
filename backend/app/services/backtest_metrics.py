"""Backtest metrics — pure functions computed from an equity curve and trade list.

Everything here is deterministic, dependency-free Python (stdlib only) so the
metrics can be unit-tested in isolation and reused by any reporting layer.

Inputs are duck-typed to keep this module decoupled from the backtest
dataclasses:

* ``equity_curve`` — iterable of objects with ``.date`` / ``.equity``
  attributes (or ``(date, equity)`` tuples).
* ``trades`` — iterable of objects with ``.symbol``, ``.side``, ``.qty``,
  ``.entry_price``, ``.entry_date``, ``.exit_price``, ``.exit_date``,
  ``.pnl`` and ``.r_multiple`` attributes.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

#: Trading days per year used for annualisation (daily-bar convention).
TRADING_DAYS_PER_YEAR = 252


# ── equity-curve metrics ───────────────────────────────────────────────


def _equity_values(equity_curve) -> list[float]:
    """Normalise an equity curve to a list of float portfolio values."""
    values: list[float] = []
    for point in equity_curve:
        if hasattr(point, "equity"):
            values.append(float(point.equity))
        else:
            values.append(float(point[1]))
    return values


def _daily_returns(equity_values: list[float]) -> list[float]:
    returns: list[float] = []
    for i in range(1, len(equity_values)):
        prev = equity_values[i - 1]
        if prev > 0:
            returns.append(equity_values[i] / prev - 1.0)
    return returns


def total_return_pct(equity_values: list[float]) -> float:
    """Total return over the whole curve, as a percentage."""
    if not equity_values or equity_values[0] <= 0:
        return 0.0
    return (equity_values[-1] / equity_values[0] - 1.0) * 100.0


def cagr_pct(
    equity_values: list[float],
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compound annual growth rate over the curve, as a percentage."""
    n = len(equity_values) - 1
    if n <= 0 or equity_values[0] <= 0 or equity_values[-1] <= 0:
        return 0.0
    return ((equity_values[-1] / equity_values[0]) ** (trading_days / n) - 1.0) * 100.0


def sharpe_ratio(
    equity_values: list[float],
    trading_days: int = TRADING_DAYS_PER_YEAR,
    risk_free: float = 0.0,
) -> float:
    """Annualised Sharpe ratio of daily returns (risk-free rate = 0 by default)."""
    returns = _daily_returns(equity_values)
    if len(returns) < 2:
        return 0.0
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns)  # sample std (ddof=1)
    if std == 0:
        return 0.0
    return (mean - risk_free / trading_days) / std * math.sqrt(trading_days)


def max_drawdown(equity_values: list[float]) -> tuple[float, int]:
    """Return ``(max_drawdown_pct, duration_days)``.

    ``max_drawdown_pct`` is the largest peak-to-trough decline as a positive
    percentage. ``duration_days`` is the number of trading days from the peak
    that precedes the trough until equity recovers above that peak (or until
    the end of the curve if it never recovers).
    """
    if len(equity_values) < 2:
        return 0.0, 0

    peak = equity_values[0]
    peak_idx = 0
    max_dd = 0.0
    trough_idx = 0
    for i, value in enumerate(equity_values):
        if value > peak:
            peak = value
            peak_idx = i
        dd = (value - peak) / peak if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            trough_idx = i

    if max_dd >= 0:
        return 0.0, 0

    peak_value = equity_values[peak_idx]
    recovery_idx: int | None = None
    for j in range(trough_idx + 1, len(equity_values)):
        if equity_values[j] >= peak_value:
            recovery_idx = j
            break
    if recovery_idx is not None:
        duration = recovery_idx - peak_idx
    else:
        duration = len(equity_values) - 1 - peak_idx
    return abs(max_dd) * 100.0, duration


# ── trade metrics ──────────────────────────────────────────────────────


def _trade_pnls(trades) -> list[float]:
    return [float(t.pnl) for t in trades]


def _per_trade_return_pct(trade) -> float:
    """Return on capital for a single trade (%), signed by direction."""
    entry = float(trade.entry_price)
    if entry <= 0:
        return 0.0
    side = str(getattr(trade, "side", "long")).lower()
    if side == "short":
        return (float(trade.entry_price) - float(trade.exit_price)) / entry * 100.0
    return (float(trade.exit_price) - entry) / entry * 100.0


def _holding_days(trade) -> float:
    """Holding period in days (min 1 for same-day round trips)."""
    try:
        delta = trade.exit_date - trade.entry_date
        days = delta.days
    except Exception:  # pragma: no cover — defensive for odd inputs
        days = 0
    return float(max(1, days + 1))


# ── main entry point ───────────────────────────────────────────────────


def compute_metrics(
    equity_curve,
    trades,
    initial_balance,
    *,
    avg_exposure_pct: float | None = None,
    trading_days: int = TRADING_DAYS_PER_YEAR,
    risk_free: float = 0.0,
) -> dict:
    """Compute the full metrics dict for a backtest result.

    Args:
        equity_curve: Daily equity series (see module docstring for shape).
        trades: Completed trades (see module docstring for shape).
        initial_balance: Starting portfolio value.
        avg_exposure_pct: Optional average portfolio exposure over the run;
            ``None`` falls back to 0.0.

    Returns:
        A dict with ``total_return_pct``, ``cagr_pct``, ``sharpe_ratio``,
        ``max_drawdown_pct``, ``max_drawdown_duration_days``, ``win_rate_pct``,
        ``profit_factor``, ``avg_trade_return_pct``, ``avg_win``, ``avg_loss``,
        ``expectancy``, ``mean_r_multiple``, ``trade_count``,
        ``avg_holding_days``, ``exposure_pct`` and ``per_symbol``.
    """
    equity_values = _equity_values(equity_curve)
    pnls = _trade_pnls(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    trade_count = len(pnls)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = None  # no losing trades — undefined
    else:
        profit_factor = 0.0

    r_multiples = [float(t.r_multiple) for t in trades if getattr(t, "r_multiple", None) is not None]

    per_symbol: dict[str, dict] = {}
    for trade in trades:
        symbol = str(trade.symbol).upper()
        entry = per_symbol.setdefault(
            symbol, {"trade_count": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "_r": []}
        )
        entry["trade_count"] += 1
        pnl = float(trade.pnl)
        entry["total_pnl"] += pnl
        if pnl > 0:
            entry["wins"] += 1
        elif pnl < 0:
            entry["losses"] += 1
        if getattr(trade, "r_multiple", None) is not None:
            entry["_r"].append(float(trade.r_multiple))
    for symbol, entry in per_symbol.items():
        entry["win_rate_pct"] = (
            entry["wins"] / entry["trade_count"] * 100.0 if entry["trade_count"] else 0.0
        )
        entry["avg_r_multiple"] = (
            sum(entry["_r"]) / len(entry["_r"]) if entry["_r"] else 0.0
        )
        entry.pop("wins", None)
        entry.pop("losses", None)
        entry.pop("_r", None)

    return {
        "total_return_pct": total_return_pct(equity_values),
        "cagr_pct": cagr_pct(equity_values, trading_days),
        "sharpe_ratio": sharpe_ratio(equity_values, trading_days, risk_free),
        "max_drawdown_pct": max_drawdown(equity_values)[0],
        "max_drawdown_duration_days": max_drawdown(equity_values)[1],
        "win_rate_pct": (len(wins) / trade_count * 100.0) if trade_count else 0.0,
        "profit_factor": profit_factor,
        "avg_trade_return_pct": (
            sum(_per_trade_return_pct(t) for t in trades) / trade_count if trade_count else 0.0
        ),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "expectancy": (sum(pnls) / trade_count) if trade_count else 0.0,
        "mean_r_multiple": (sum(r_multiples) / len(r_multiples)) if r_multiples else 0.0,
        "trade_count": trade_count,
        "avg_holding_days": (
            sum(_holding_days(t) for t in trades) / trade_count if trade_count else 0.0
        ),
        "exposure_pct": avg_exposure_pct if avg_exposure_pct is not None else 0.0,
        "per_symbol": per_symbol,
        "initial_balance": float(initial_balance),
    }
