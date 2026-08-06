"""Backtesting engine — replay strategy signals on historical daily bars.

The backtest walks forward day by day, calls the *same* ``analyze()`` method
the live scanner uses, fills trades at the next day's open (never at the
signal close — no lookahead), and manages every position with intrabar
stop-loss / take-profit checks against each day's high/low.

Execution conventions:

* Signals fill at the *next* trading day's open.
* A fill that gaps through the stop (open beyond the stop level, e.g. a long
  entry opening below its stop) is dropped — the intended risk/reward no
  longer exists, so the signal is void.
* Stop-loss is checked before take-profit intrabar (conservative when one bar
  touches both); a gap through a level fills at the open.

It is deliberately in-memory: positions and trades are plain dataclasses, the
database is only used to load strategy config and fetch market data, and no
paper-trading tables are touched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import Strategy
from app.schemas.market_data import Bar
from app.services.auto_trade import DEFAULT_RISK_PER_TRADE, _compute_quantity
from app.services.backtest_metrics import compute_metrics
from app.services.market_data import get_daily_bars
from app.strategies import StrategySignal, get_strategy

logger = logging.getLogger(__name__)

#: Bars skipped at the start of each symbol's series (indicator warmup).
#: Strategies return ``None`` while indicators are warming up anyway; this
#: simply avoids calling ``analyze()`` before there is enough data.
WARMUP_BARS = 60

EXIT_STOP_LOSS = "stop_loss"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_END = "end_of_backtest"

TRADEABLE_ACTIONS = ("buy", "sell")


# ── result dataclasses (in-memory only) ────────────────────────────────


@dataclass
class EquityPoint:
    """One point on the equity curve — portfolio value at a bar's close."""

    date: datetime
    equity: Decimal


@dataclass
class BacktestTrade:
    """A completed round-trip trade."""

    symbol: str
    side: str  # "long" | "short"
    qty: Decimal
    entry_price: Decimal
    entry_date: datetime
    exit_price: Decimal
    exit_date: datetime
    pnl: Decimal
    r_multiple: float | None
    exit_reason: str


@dataclass
class BacktestResult:
    """Full result of a backtest run."""

    strategy_name: str
    symbols: list[str]
    start_date: datetime
    end_date: datetime
    initial_balance: Decimal
    final_balance: Decimal
    equity_curve: list[EquityPoint]
    trades: list[BacktestTrade]
    metrics: dict


# ── internal state ─────────────────────────────────────────────────────


@dataclass
class _Position:
    """An open position held by the backtest (not persisted)."""

    symbol: str
    side: str  # "long" | "short"
    qty: Decimal
    entry_price: Decimal
    entry_date: datetime
    stop_loss: Decimal | None
    take_profit: Decimal | None
    last_close: Decimal  # last known close, used for mark-to-market


@dataclass
class _PendingOrder:
    """A signal waiting to be filled at the next day's open."""

    symbol: str
    action: str  # "buy" | "sell"
    fill_date: date
    stop_loss: Decimal | None
    take_profit: Decimal | None


# ── helpers ────────────────────────────────────────────────────────────


def _bar_date(bar: Bar) -> date:
    return bar.timestamp.date()


def _slip(price: Decimal, action: str, slippage: float) -> Decimal:
    """Apply slippage (fraction of price) against the trader.

    A buy fills higher, a sell fills lower. ``slippage=0.0`` (the default)
    leaves the price untouched.
    """
    if slippage <= 0:
        return price
    factor = Decimal(str(1 + slippage)) if action == "buy" else Decimal(str(1 - slippage))
    return price * factor


def _intrabar_exit(pos: _Position, bar: Bar, slippage: float) -> tuple[Decimal, str] | None:
    """Return ``(exit_price, reason)`` if *bar*'s intrabar range triggers an exit.

    Stop-loss is checked before take-profit (conservative: if a bar touches
    both, the stop wins). Gaps through a level fill at the bar's open, which is
    the realistic execution when price opens beyond the stop/limit.
    """
    stop = pos.stop_loss
    tp = pos.take_profit

    if pos.side == "long":
        if stop is not None:
            if bar.open <= stop:
                return _slip(bar.open, "sell", slippage), EXIT_STOP_LOSS
            if bar.low <= stop:
                return _slip(stop, "sell", slippage), EXIT_STOP_LOSS
        if tp is not None:
            if bar.open >= tp:
                return _slip(bar.open, "sell", slippage), EXIT_TAKE_PROFIT
            if bar.high >= tp:
                return _slip(tp, "sell", slippage), EXIT_TAKE_PROFIT
    else:  # short
        if stop is not None:
            if bar.open >= stop:
                return _slip(bar.open, "buy", slippage), EXIT_STOP_LOSS
            if bar.high >= stop:
                return _slip(stop, "buy", slippage), EXIT_STOP_LOSS
        if tp is not None:
            if bar.open <= tp:
                return _slip(bar.open, "buy", slippage), EXIT_TAKE_PROFIT
            if bar.low <= tp:
                return _slip(tp, "buy", slippage), EXIT_TAKE_PROFIT
    return None


def _close_pnl(pos: _Position, exit_price: Decimal) -> tuple[Decimal, float | None]:
    """Return ``(pnl, r_multiple)`` for closing *pos* at *exit_price*."""
    if pos.side == "long":
        pnl = (exit_price - pos.entry_price) * pos.qty
        risk_per_share = (
            pos.entry_price - pos.stop_loss if pos.stop_loss is not None else None
        )
    else:
        pnl = (pos.entry_price - exit_price) * pos.qty
        risk_per_share = (
            pos.stop_loss - pos.entry_price if pos.stop_loss is not None else None
        )
    r_multiple: float | None = None
    if risk_per_share is not None and risk_per_share > 0:
        r_multiple = float(pnl / (pos.qty * risk_per_share))
    return pnl, r_multiple


async def _load_strategy(
    db: AsyncSession,
    strategy_name: str,
    config_overrides: dict | None,
):
    """Load a named strategy from the DB with ``config_overrides`` on top."""
    result = await db.execute(
        select(Strategy).where(
            Strategy.name == strategy_name,
            Strategy.is_enabled == True,  # noqa: E712
        )
    )
    db_strategy = result.scalar_one_or_none()
    if db_strategy is None:
        raise ValueError(f"Strategy '{strategy_name}' not found or disabled")
    config = dict(db_strategy.config or {})
    if config_overrides:
        config.update(config_overrides)
    return get_strategy(strategy_name, config=config)


# ── main entry point ───────────────────────────────────────────────────


async def run_backtest(
    db: AsyncSession,
    strategy_name: str,
    symbols: list[str],
    start_date: datetime,
    end_date: datetime,
    *,
    config_overrides: dict | None = None,
    risk_per_trade: float | Decimal = DEFAULT_RISK_PER_TRADE,
    max_open_positions: int = 10,
    max_portfolio_exposure_pct: float = 80.0,
    slippage: float = 0.0,
    initial_balance: float | Decimal = 100_000,
) -> BacktestResult:
    """Run a backtest of *strategy_name* over the given range and symbols.

    Args:
        db: Async DB session (used only for strategy config + market data).
        strategy_name: Registered strategy name, e.g. ``"trend_following"``.
        symbols: Tickers to include.
        start_date / end_date: Backtest window (daily bars).
        config_overrides: Strategy config merged over the DB config.
        risk_per_trade: Fraction of equity risked per trade (0.5% default).
        max_open_positions: Hard cap on concurrently open positions.
        max_portfolio_exposure_pct: Hard cap on gross position value / equity.
        slippage: Execution slippage as a fraction of price (0.0 default).
        initial_balance: Starting cash.

    Returns:
        A :class:`BacktestResult` with the equity curve, trades and metrics.

    Raises:
        ValueError: If the strategy is unknown/disabled or no data is returned.
    """
    strategy = await _load_strategy(db, strategy_name, config_overrides)
    initial = Decimal(str(initial_balance))

    # ── 1. Fetch and index daily bars per symbol ─────────────────────
    bar_map: dict[str, list[Bar]] = {}
    for symbol in symbols:
        bars = await get_daily_bars(db, symbol, start_date, end_date)
        # Defensive: dedupe by date, sort ascending, clip to the window.
        by_date: dict[date, Bar] = {}
        for bar in bars:
            day = _bar_date(bar)
            if start_date.date() <= day <= end_date.date():
                by_date.setdefault(day, bar)
        if by_date:
            bar_map[symbol.upper()] = [by_date[d] for d in sorted(by_date)]

    if not bar_map:
        raise ValueError("No market data returned for any symbol in the requested range")

    sym_index: dict[str, dict[date, int]] = {}
    sym_next: dict[str, dict[date, date]] = {}
    day_max_ts: dict[date, datetime] = {}
    all_dates: set[date] = set()
    for symbol, bars in bar_map.items():
        idx: dict[date, int] = {}
        for i, bar in enumerate(bars):
            day = _bar_date(bar)
            idx[day] = i
            ts = bar.timestamp
            if day not in day_max_ts or ts > day_max_ts[day]:
                day_max_ts[day] = ts
        sym_index[symbol] = idx
        sym_next[symbol] = {
            _bar_date(bars[i - 1]): _bar_date(bars[i]) for i in range(1, len(bars))
        }
        all_dates.update(idx)

    ordered_dates = sorted(all_dates)

    # ── 2. Walk forward day by day ────────────────────────────────────
    cash = initial
    positions: dict[str, _Position] = {}
    pending: list[_PendingOrder] = []
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    exposure_samples: list[float] = []

    def portfolio_equity() -> tuple[Decimal, Decimal]:
        """Return ``(equity, gross_exposure)`` using each position's last close."""
        equity = cash
        exposure = Decimal("0")
        for pos in positions.values():
            mark = pos.qty * pos.last_close
            equity = equity + mark if pos.side == "long" else equity - mark
            exposure += mark
        return equity, exposure

    def close_position(symbol: str, exit_price: Decimal, exit_date: datetime, reason: str) -> None:
        """Close an open position, settle cash, and record the trade."""
        nonlocal cash
        pos = positions.pop(symbol)
        pnl, r_multiple = _close_pnl(pos, exit_price)
        if pos.side == "long":
            cash += pos.qty * exit_price
        else:
            cash -= pos.qty * exit_price
        trades.append(
            BacktestTrade(
                symbol=pos.symbol,
                side=pos.side,
                qty=pos.qty,
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                exit_price=exit_price,
                exit_date=exit_date,
                pnl=pnl,
                r_multiple=r_multiple,
                exit_reason=reason,
            )
        )

    for day in ordered_dates:
        # (A) Fill pending orders at today's open
        if pending:
            equity, exposure = portfolio_equity()
            still_pending: list[_PendingOrder] = []
            for order in pending:
                if day != order.fill_date:
                    still_pending.append(order)
                    continue
                i = sym_index[order.symbol].get(day)
                if i is None:
                    still_pending.append(order)
                    continue
                if order.symbol in positions or len(positions) >= max_open_positions:
                    continue
                bar = bar_map[order.symbol][i]
                entry = _slip(bar.open, order.action, slippage)
                qty = _compute_quantity(
                    entry, equity, None, order.action, order.stop_loss, risk_per_trade
                )
                if qty <= 0:
                    continue  # cannot risk-size the position — drop the order
                new_exposure = exposure + qty * entry
                if equity > 0 and (
                    new_exposure / equity * Decimal("100") > Decimal(str(max_portfolio_exposure_pct))
                ):
                    continue  # would breach the portfolio exposure cap
                side = "long" if order.action == "buy" else "short"
                if side == "long":
                    cash -= qty * entry
                else:
                    cash += qty * entry
                positions[order.symbol] = _Position(
                    symbol=order.symbol,
                    side=side,
                    qty=qty,
                    entry_price=entry,
                    entry_date=bar.timestamp,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    last_close=entry,  # marked at the open until today's close is known
                )
                equity, exposure = portfolio_equity()
            pending = still_pending

        # (B) Intrabar stop-loss / take-profit check against today's range
        for symbol in list(positions.keys()):
            i = sym_index[symbol].get(day)
            if i is None:
                continue  # symbol has no bar today — no intrabar move
            bar = bar_map[symbol][i]
            exit_info = _intrabar_exit(positions[symbol], bar, slippage)
            if exit_info is not None:
                exit_price, reason = exit_info
                close_position(symbol, exit_price, bar.timestamp, reason)

        # (C) Mark to market at today's close
        for symbol, pos in positions.items():
            i = sym_index[symbol].get(day)
            if i is not None:
                pos.last_close = bar_map[symbol][i].close
        equity, exposure = portfolio_equity()
        if equity > 0:
            exposure_samples.append(float(exposure / equity * 100))
        equity_curve.append(EquityPoint(date=day_max_ts[day], equity=equity))

        # (D) Analyze at today's close → signal fills at the next open
        if len(positions) < max_open_positions:
            for symbol, bars in bar_map.items():
                if symbol in positions:
                    continue
                idx = sym_index[symbol].get(day)
                if idx is None or idx < WARMUP_BARS:
                    continue
                if day not in sym_next[symbol]:
                    continue  # no next trading day → cannot fill without lookahead
                try:
                    signal = await strategy.analyze(symbol, bars[: idx + 1])
                except Exception:
                    logger.exception(
                        "Backtest analyze failed for %s (%s) — skipping signal.",
                        symbol, strategy_name,
                    )
                    continue
                if signal is None or not _is_tradeable(signal):
                    continue
                stop = (
                    Decimal(str(signal.stop_loss))
                    if signal.stop_loss is not None else None
                )
                if stop is None:
                    continue  # risk-based sizing requires a stop
                pending.append(
                    _PendingOrder(
                        symbol=symbol,
                        action=signal.action,
                        fill_date=sym_next[symbol][day],
                        stop_loss=stop,
                        take_profit=(
                            Decimal(str(signal.take_profit))
                            if signal.take_profit is not None else None
                        ),
                    )
                )

    # ── 3. Liquidate anything still open at the final close ──────────
    for symbol in list(positions.keys()):
        last_bar = bar_map[symbol][-1]
        exit_price = _slip(
            last_bar.close,
            "sell" if positions[symbol].side == "long" else "buy",
            slippage,
        )
        close_position(symbol, exit_price, last_bar.timestamp, EXIT_END)

    final_balance = cash  # all positions are closed, so cash == equity

    avg_exposure = sum(exposure_samples) / len(exposure_samples) if exposure_samples else 0.0
    metrics = compute_metrics(
        equity_curve,
        trades,
        initial,
        avg_exposure_pct=avg_exposure,
    )

    return BacktestResult(
        strategy_name=strategy_name,
        symbols=symbols,
        start_date=start_date,
        end_date=end_date,
        initial_balance=initial,
        final_balance=final_balance,
        equity_curve=equity_curve,
        trades=trades,
        metrics=metrics,
    )


def _is_tradeable(signal: StrategySignal) -> bool:
    """True if a signal can be traded: clean, directional, priced."""
    if signal.error is not None or signal.action not in TRADEABLE_ACTIONS:
        return False
    if signal.entry_price is None or signal.entry_price <= 0:
        return False
    return True
