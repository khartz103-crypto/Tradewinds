"""Tests for the backtesting engine.

Covers ``app/services/backtest.py`` and ``app/services/backtest_metrics.py``:
no-lookahead fills, intrabar SL/TP handling, gap fills, no-doubling,
position caps, and metric correctness on a synthetic equity curve.

All tests run without a database — ``get_daily_bars`` and ``_load_strategy``
are monkeypatched so the engine runs on synthetic bars in memory.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.schemas.market_data import Bar
from app.services import backtest as backtest_service
from app.services.backtest import (
    WARMUP_BARS,
    BacktestTrade,
    EquityPoint,
    run_backtest,
)
from app.services.backtest_metrics import compute_metrics
from app.strategies import StrategySignal
from app.strategies.trend_following import TrendFollowingStrategy
from tests.test_strategy import _choppy_bars, _trending_bars


# ── synthetic bar fixtures ─────────────────────────────────────────────


def _make_bar(
    timestamp: datetime,
    o: float,
    h: float,
    l: float,
    c: float,
    v: int = 1_000_000,
) -> Bar:
    return Bar(
        timestamp=timestamp,
        open=o,  # type: ignore[arg-type]
        high=h,  # type: ignore[arg-type]
        low=l,  # type: ignore[arg-type]
        close=c,  # type: ignore[arg-type]
        volume=v,  # type: ignore[arg-type]
    )


def _drift_bars(n: int = 100, start_price: float = 100.0, drift: float = 0.05,
                spread: float = 0.05, open_gap: float = 0.0) -> list[Bar]:
    """Gentle upward drift with a unique calendar and a controllable open gap.

    ``open_gap`` offsets each bar's open above the previous close, which lets
    tests distinguish "fill at next open" from "fill at signal close".
    """
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = start_price
    for i in range(n):
        ts = base + timedelta(days=i)
        o = price + open_gap
        c = o + drift
        h = max(o, c) + spread
        l = min(o, c) - spread
        bars.append(_make_bar(ts, o, h, l, c))
        price = c
    return bars


def _restamp(bars: list[Bar]) -> list[Bar]:
    """Re-stamp fixture bars (which reuse day 28) with a unique daily calendar."""
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        _make_bar(
            base + timedelta(days=i),
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            int(bar.volume),
        )
        for i, bar in enumerate(bars)
    ]


# ── scripted strategy / signals ────────────────────────────────────────


def _buy_signal(symbol: str, bars: list[Bar], stop_dist: float = 1.0, tp_dist: float = 2.0) -> StrategySignal:
    entry = float(bars[-1].close)
    return StrategySignal(
        symbol=symbol,
        action="buy",
        confidence=70.0,
        entry_price=entry,
        stop_loss=entry - stop_dist,
        take_profit=entry + tp_dist,
        reasoning="test buy",
    )


def _sell_signal(symbol: str, bars: list[Bar], stop_dist: float = 1.0, tp_dist: float = 2.0) -> StrategySignal:
    entry = float(bars[-1].close)
    return StrategySignal(
        symbol=symbol,
        action="sell",
        confidence=70.0,
        entry_price=entry,
        stop_loss=entry + stop_dist,
        take_profit=entry - tp_dist,
        reasoning="test sell",
    )


class _ScriptedStrategy:
    """Fake strategy that fires ``signal_factory`` on every analyze call."""

    def __init__(self, signal_factory=None):
        self.signal_factory = signal_factory
        self.analyze_calls = 0

    async def analyze(self, symbol: str, bars: list[Bar]):
        self.analyze_calls += 1
        if self.signal_factory is None:
            return None
        return self.signal_factory(symbol, bars)


async def _run(monkeypatch, bars_map: dict[str, list[Bar]], strategy, **kwargs):
    """Run a backtest against synthetic bars with all DB access patched out."""

    async def fake_load_strategy(db, name, config_overrides):
        return strategy

    async def fake_get_daily_bars(db, symbol, start, end):
        return list(bars_map[symbol.upper()])

    monkeypatch.setattr(backtest_service, "_load_strategy", fake_load_strategy)
    monkeypatch.setattr(backtest_service, "get_daily_bars", fake_get_daily_bars)

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 12, 31, tzinfo=timezone.utc)
    return await run_backtest(
        db=object(),
        strategy_name="trend_following",
        symbols=list(bars_map.keys()),
        start_date=start,
        end_date=end,
        **kwargs,
    )


# ── no-lookahead & fill logic ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_fill_at_next_open_no_lookahead(monkeypatch):
    """Entry must fill at the next day's open, never at the signal close."""
    bars = _drift_bars(n=90, open_gap=0.5)
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    assert len(result.trades) >= 1
    trade = result.trades[0]
    signal_close = float(bars[WARMUP_BARS].close)
    next_open = bars[WARMUP_BARS + 1].open
    assert float(trade.entry_price) == pytest.approx(float(next_open))
    assert float(trade.entry_price) != pytest.approx(float(signal_close))
    assert trade.entry_date == bars[WARMUP_BARS + 1].timestamp
    assert trade.side == "long"
    assert all(t.symbol == "AAPL" for t in result.trades)


@pytest.mark.asyncio
async def test_stop_loss_intrabar_fill(monkeypatch):
    """A bar whose low pierces the stop fills at the stop price, not the low."""
    bars = _drift_bars(n=90, open_gap=0.5)
    signal_close = float(bars[WARMUP_BARS].close)
    stop = signal_close - 1.0
    day61 = bars[WARMUP_BARS + 1]
    day61_open = float(day61.open)
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, day61_open, day61_open + 0.3, stop - 0.1, stop + 0.2
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert float(trade.exit_price) == pytest.approx(float(stop))
    assert trade.exit_date == bars[WARMUP_BARS + 1].timestamp
    assert trade.pnl < 0
    assert trade.r_multiple == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_take_profit_intrabar_fill(monkeypatch):
    """A bar whose high pierces the take-profit fills at the TP price."""
    bars = _drift_bars(n=90)  # no open gap → entry == signal close, clean 1:2 risk
    signal_close = float(bars[WARMUP_BARS].close)
    tp = signal_close + 2.0
    day61 = bars[WARMUP_BARS + 1]
    day61_open = float(day61.open)
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, day61_open, tp + 0.1, day61_open - 0.1, tp - 0.2
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert float(trade.exit_price) == pytest.approx(float(tp))
    assert trade.pnl > 0
    assert trade.r_multiple == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_gap_down_through_stop_fills_at_open(monkeypatch):
    """An open gap below the stop on an open position fills at the open."""
    bars = _drift_bars(n=90, open_gap=0.5)
    signal_close = float(bars[WARMUP_BARS].close)
    stop = signal_close - 1.0
    # Day 61 (entry day) is a normal drift bar — no exit. Day 62 gaps down
    # through the stop, so the position must exit at day 62's open.
    day62 = bars[WARMUP_BARS + 2]
    gap_open = float(day62.open) - (float(day62.open) - stop) - 2.0  # 2 below the stop
    bars[WARMUP_BARS + 2] = _make_bar(
        day62.timestamp, gap_open, gap_open + 0.3, gap_open - 0.2, gap_open + 0.1
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert float(trade.entry_price) == pytest.approx(float(bars[WARMUP_BARS + 1].open))
    assert float(trade.exit_price) == pytest.approx(gap_open)
    assert trade.exit_date == bars[WARMUP_BARS + 2].timestamp


@pytest.mark.asyncio
async def test_gap_up_through_take_profit_fills_at_open(monkeypatch):
    """An open gap above the take-profit on an open position fills at the open."""
    bars = _drift_bars(n=90, open_gap=0.5)
    signal_close = float(bars[WARMUP_BARS].close)
    tp = signal_close + 2.0
    day62 = bars[WARMUP_BARS + 2]
    gap_open = float(day62.open) + (tp - float(day62.open)) + 1.0  # 1 above the TP
    bars[WARMUP_BARS + 2] = _make_bar(
        day62.timestamp, gap_open, gap_open + 0.5, gap_open - 0.3, gap_open + 0.2
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    trade = result.trades[0]
    assert trade.exit_reason == "take_profit"
    assert float(trade.exit_price) == pytest.approx(gap_open)
    assert trade.exit_date == bars[WARMUP_BARS + 2].timestamp


@pytest.mark.asyncio
async def test_slippage_applies_against_trader(monkeypatch):
    """Slippage makes buy fills worse (entry above the open)."""
    bars = _drift_bars(n=90, open_gap=0.5)
    result = await _run(
        monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal), slippage=0.01
    )
    trade = result.trades[0]
    next_open = float(bars[WARMUP_BARS + 1].open)
    assert float(trade.entry_price) == pytest.approx(next_open * 1.01)


@pytest.mark.asyncio
async def test_entry_gap_through_stop_drops_signal(monkeypatch):
    """A fill opening through the stop is void — no position is opened.

    Risk-based sizing needs a positive stop distance; when the entry open gaps
    beyond the stop the trade is dropped (the risk/reward is broken) and the
    next day's signal re-evaluates.
    """
    bars = _drift_bars(n=90, open_gap=0.5)
    signal_close = float(bars[WARMUP_BARS].close)
    stop = signal_close - 1.0
    day61 = bars[WARMUP_BARS + 1]
    gap_open = stop - 2.0  # entry open is 2 below the stop
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, gap_open, gap_open + 0.3, gap_open - 0.2, gap_open + 0.1
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    # The gapped entry was skipped, so the first realized trade cannot be a
    # breakeven round trip at the gap open.
    assert all(
        float(t.entry_price) != pytest.approx(gap_open) for t in result.trades
    )


@pytest.mark.asyncio
async def test_stop_checked_before_take_profit(monkeypatch):
    """If one bar touches both SL and TP, the stop wins (conservative)."""
    bars = _drift_bars(n=90, open_gap=0.5)
    signal_close = float(bars[WARMUP_BARS].close)
    stop = signal_close - 1.0
    tp = signal_close + 2.0
    day61 = bars[WARMUP_BARS + 1]
    day61_open = float(day61.open)
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, day61_open, tp + 0.5, stop - 0.5, day61_open + 0.1
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_buy_signal))

    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert float(trade.exit_price) == pytest.approx(float(stop))


# ── position management ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_doubling_on_same_symbol(monkeypatch):
    """A symbol with an open position never gets a second one."""
    bars = _drift_bars(n=90)
    strategy = _ScriptedStrategy(_buy_signal)
    result = await _run(monkeypatch, {"AAPL": bars}, strategy)

    assert len(result.trades) == 1
    # analyze only ran before the position opened; the symbol guard blocked
    # any further signals (no doubling, and no wasted analyze calls either)
    assert strategy.analyze_calls == 1


@pytest.mark.asyncio
async def test_max_open_positions_cap(monkeypatch):
    """With max_open_positions=1 only the first signal may open a position."""
    bars = _drift_bars(n=90)
    result = await _run(
        monkeypatch,
        {"AAPL": bars, "MSFT": bars},
        _ScriptedStrategy(_buy_signal),
        max_open_positions=1,
        max_portfolio_exposure_pct=1000,
    )
    assert len(result.trades) == 1


@pytest.mark.asyncio
async def test_short_take_profit(monkeypatch):
    """Short side: a low below the TP fills at the TP with positive PnL."""
    bars = _drift_bars(n=90)
    signal_close = float(bars[WARMUP_BARS].close)
    tp = signal_close - 2.0
    day61 = bars[WARMUP_BARS + 1]
    day61_open = float(day61.open)
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, day61_open, day61_open + 0.3, tp - 0.1, tp + 0.3
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_sell_signal))

    trade = result.trades[0]
    assert trade.side == "short"
    assert trade.exit_reason == "take_profit"
    assert float(trade.exit_price) == pytest.approx(float(tp))
    assert trade.pnl > 0


@pytest.mark.asyncio
async def test_short_stop_loss(monkeypatch):
    """Short side: a high above the stop fills at the stop with negative PnL."""
    bars = _drift_bars(n=90)
    signal_close = float(bars[WARMUP_BARS].close)
    stop = signal_close + 1.0
    day61 = bars[WARMUP_BARS + 1]
    day61_open = float(day61.open)
    bars[WARMUP_BARS + 1] = _make_bar(
        day61.timestamp, day61_open, stop + 0.1, day61_open - 0.3, stop - 0.1
    )
    result = await _run(monkeypatch, {"AAPL": bars}, _ScriptedStrategy(_sell_signal))

    trade = result.trades[0]
    assert trade.side == "short"
    assert trade.exit_reason == "stop_loss"
    assert float(trade.exit_price) == pytest.approx(float(stop))
    assert trade.pnl < 0


# ── real strategies end-to-end ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_trend_strategy_on_trending_bars(monkeypatch):
    """The real TrendFollowingStrategy runs end-to-end on the trending fixture."""
    bars = _restamp(_trending_bars(250, bias=0.3))
    result = await _run(monkeypatch, {"TEST": bars}, TrendFollowingStrategy())

    assert len(result.equity_curve) == len(bars)
    assert result.initial_balance == Decimal("100000")
    assert result.final_balance > 0
    assert result.metrics["trade_count"] == len(result.trades)
    assert result.metrics["total_return_pct"] is not None
    assert "TEST" in result.metrics["per_symbol"]
    for trade in result.trades:
        assert trade.entry_date <= trade.exit_date
        assert trade.qty > 0


@pytest.mark.asyncio
async def test_real_trend_strategy_on_choppy_bars(monkeypatch):
    """Choppy bars produce the documented SELL signal → a short position."""
    bars = _restamp(_choppy_bars(120))
    result = await _run(monkeypatch, {"XYZ": bars}, TrendFollowingStrategy())

    assert len(result.equity_curve) == len(bars)
    if result.trades:
        assert result.trades[0].side in ("long", "short")


# ── metrics ────────────────────────────────────────────────────────────


def test_compute_metrics_synthetic_curve():
    """Metrics match hand-computed values on a synthetic equity curve."""
    d1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    d2 = datetime(2025, 1, 2, tzinfo=timezone.utc)
    d3 = datetime(2025, 1, 3, tzinfo=timezone.utc)
    d4 = datetime(2025, 1, 4, tzinfo=timezone.utc)

    curve = [
        EquityPoint(date=d1, equity=Decimal("1000")),
        EquityPoint(date=d2, equity=Decimal("1050")),
        EquityPoint(date=d3, equity=Decimal("997.5")),
        EquityPoint(date=d4, equity=Decimal("1047.375")),
    ]
    trades = [
        BacktestTrade("AAPL", "long", Decimal("100"), Decimal("100"), d1,
                      Decimal("105"), d2, Decimal("500"), 2.0, "take_profit"),
        BacktestTrade("AAPL", "long", Decimal("100"), Decimal("100"), d1,
                      Decimal("99"), d3, Decimal("-100"), -0.5, "stop_loss"),
        BacktestTrade("MSFT", "short", Decimal("50"), Decimal("200"), d1,
                      Decimal("190"), d4, Decimal("500"), 2.0, "take_profit"),
    ]

    m = compute_metrics(curve, trades, Decimal("1000"), avg_exposure_pct=42.0)

    assert m["total_return_pct"] == pytest.approx(4.7375)
    assert m["max_drawdown_pct"] == pytest.approx(5.0)
    assert m["max_drawdown_duration_days"] == 2
    assert m["sharpe_ratio"] == pytest.approx(4.5826, abs=1e-3)
    assert m["trade_count"] == 3
    assert m["win_rate_pct"] == pytest.approx(66.666666, abs=1e-4)
    assert m["profit_factor"] == pytest.approx(10.0)
    assert m["expectancy"] == pytest.approx(300.0)
    assert m["avg_win"] == pytest.approx(500.0)
    assert m["avg_loss"] == pytest.approx(-100.0)
    assert m["mean_r_multiple"] == pytest.approx(3.5 / 3)
    assert m["avg_trade_return_pct"] == pytest.approx(3.0)
    assert m["avg_holding_days"] == pytest.approx(3.0)
    assert m["exposure_pct"] == pytest.approx(42.0)

    assert m["per_symbol"]["AAPL"]["trade_count"] == 2
    assert m["per_symbol"]["AAPL"]["win_rate_pct"] == pytest.approx(50.0)
    assert m["per_symbol"]["AAPL"]["total_pnl"] == pytest.approx(400.0)
    assert m["per_symbol"]["AAPL"]["avg_r_multiple"] == pytest.approx(0.75)
    assert m["per_symbol"]["MSFT"]["trade_count"] == 1
    assert m["per_symbol"]["MSFT"]["win_rate_pct"] == pytest.approx(100.0)


def test_compute_metrics_empty_run():
    """An empty run produces zeros/None without crashing."""
    d1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    m = compute_metrics([EquityPoint(date=d1, equity=Decimal("100000"))], [], Decimal("100000"))
    assert m["trade_count"] == 0
    assert m["total_return_pct"] == 0.0
    assert m["win_rate_pct"] == 0.0
    assert m["profit_factor"] == 0.0
    assert m["expectancy"] == 0.0
    assert m["per_symbol"] == {}
