"""Tests for the live profit-dashboard metrics (``app/services/dashboard.py``).

Runs without a database: the pure builders are tested directly and the async
entry point is exercised with monkeypatched row loaders, mirroring how
``test_auto_trade`` fakes DB access.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import dashboard
from app.services.dashboard import (
    build_dashboard_performance,
    build_equity_curve,
    get_dashboard_performance,
)

# ── fixtures ────────────────────────────────────────────────────────────
D1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
D2 = datetime(2025, 1, 2, tzinfo=timezone.utc)
D3 = datetime(2025, 1, 3, tzinfo=timezone.utc)
D4 = datetime(2025, 1, 4, tzinfo=timezone.utc)
NOW = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)


def _closed(symbol, side, qty, entry, exit_, pnl, entry_date, exit_date):
    """Build a fake closed Position row with the fields the service reads."""
    return SimpleNamespace(
        symbol=symbol,
        side=SimpleNamespace(value=side),
        quantity=Decimal(str(qty)),
        entry_price=Decimal(str(entry)),
        current_price=Decimal(str(exit_)),
        exit_price=Decimal(str(exit_)),
        entry_date=entry_date,
        exit_date=exit_date,
        pnl=Decimal(str(pnl)),
        created_at=entry_date,
    )


@pytest.fixture
def three_trades():
    """2 wins + 1 loss; same hand-computed shape as test_backtest."""
    return [
        _closed("AAPL", "long", 100, 100, 105, 500, D1, D2),
        _closed("AAPL", "long", 100, 100, 99, -100, D1, D3),
        _closed("MSFT", "short", 50, 200, 190, 500, D1, D4),
    ]


@pytest.fixture
def account():
    return SimpleNamespace(
        initial_balance=Decimal("100000"),
        current_balance=Decimal("100900"),
        created_at=D1,
    )


# ── equity curve ────────────────────────────────────────────────────────
def test_build_equity_curve_from_trades(three_trades, account):
    """Curve starts at initial balance and steps by cumulative P&L."""
    curve = build_equity_curve(100000.0, three_trades, 100900.0, D1, now=NOW)
    assert [round(p.equity, 2) for p in curve] == [100000.0, 100500.0, 100400.0, 100900.0, 100900.0]
    assert curve[0].date == D1
    assert curve[-1].date == NOW
    assert curve[1].date.date() == D2.date()


def test_build_equity_curve_aggregates_same_day(account):
    """Two trades closed on the same day collapse into one curve point."""
    trades = [
        _closed("AAPL", "long", 10, 100, 110, 100, D1, D2),
        _closed("MSFT", "long", 10, 100, 90, -100, D1, D2),
    ]
    curve = build_equity_curve(1000.0, trades, 1000.0, D1, now=NOW)
    assert len(curve) == 3  # start + one aggregated close day + final mark
    assert curve[1].equity == 1000.0  # +100 -100 cancels out


def test_build_equity_curve_empty(account):
    """No trades → start point + final mark-to-market point."""
    curve = build_equity_curve(100000.0, [], 100250.0, D1, now=NOW)
    assert len(curve) == 2
    assert curve[0].equity == 100000.0
    assert curve[1].equity == 100250.0


# ── payload builder ─────────────────────────────────────────────────────
def test_build_dashboard_performance_with_trades(three_trades, account):
    """Metrics match hand-computed values and per-symbol is sorted by P&L."""
    payload = build_dashboard_performance(account, three_trades, 2, 100900.0)
    assert payload["starting_balance"] == 100000.0
    assert payload["current_equity"] == 100900.0
    assert payload["total_return_pct"] == pytest.approx(0.9)
    assert payload["total_pnl"] == pytest.approx(900.0)
    assert payload["open_positions"] == 2
    assert payload["total_trades_closed"] == 3
    assert payload["win_rate_pct"] == pytest.approx(66.67, abs=0.01)
    assert payload["profit_factor"] == pytest.approx(10.0)
    assert payload["avg_holding_days"] == pytest.approx(3.0)
    assert payload["max_drawdown_pct"] == pytest.approx(0.0995, abs=0.01)
    assert payload["sharpe_ratio"] > 0
    # Per-symbol: MSFT (+500) before AAPL (+400).
    assert [s["symbol"] for s in payload["per_symbol"]] == ["MSFT", "AAPL"]
    assert payload["per_symbol"][1]["total_pnl"] == pytest.approx(400.0)
    assert payload["per_symbol"][1]["trade_count"] == 2
    assert payload["per_symbol"][1]["win_rate_pct"] == pytest.approx(50.0)
    # Recent trades: newest first.
    assert [t["symbol"] for t in payload["recent_trades"]] == ["MSFT", "AAPL", "AAPL"]
    assert payload["recent_trades"][0]["holding_days"] == 4.0
    assert payload["recent_trades"][0]["side"] == "short"
    # Equity curve mirrors the builder.
    assert len(payload["equity_curve"]) == len(build_equity_curve(100000.0, three_trades, 100900.0, D1, now=NOW))


def test_build_dashboard_performance_empty():
    """No account and no trades → zeros/defaults, never an error."""
    payload = build_dashboard_performance(None, [], 0, 100000.0)
    assert payload["starting_balance"] == 100000.0
    assert payload["current_equity"] == 100000.0
    assert payload["total_return_pct"] == 0.0
    assert payload["total_pnl"] == 0.0
    assert payload["open_positions"] == 0
    assert payload["total_trades_closed"] == 0
    assert payload["win_rate_pct"] == 0.0
    assert payload["profit_factor"] in (None, 0.0)
    assert payload["sharpe_ratio"] == 0.0
    assert payload["max_drawdown_pct"] == 0.0
    assert payload["avg_holding_days"] == 0.0
    assert payload["per_symbol"] == []
    assert payload["recent_trades"] == []


def test_profit_factor_none_when_no_losses(account):
    """All-winning runs report profit_factor as None (undefined, not 0)."""
    trades = [
        _closed("AAPL", "long", 10, 100, 110, 100, D1, D2),
        _closed("MSFT", "long", 10, 100, 110, 100, D1, D3),
    ]
    payload = build_dashboard_performance(account, trades, 0, 100200.0)
    assert payload["profit_factor"] is None
    assert payload["win_rate_pct"] == pytest.approx(100.0)


# ── async entry point ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_dashboard_performance_uses_db(three_trades, account, monkeypatch):
    """Entry point loads rows through the DB helpers and marks to market."""
    class FakeDB:
        pass

    open_positions = [
        SimpleNamespace(quantity=Decimal("10"), current_price=Decimal("100")),
        SimpleNamespace(quantity=Decimal("5"), current_price=Decimal("200")),
    ]
    monkeypatch.setattr(dashboard, "_load_account", _async_return(account))
    monkeypatch.setattr(dashboard, "_load_closed_positions", _async_return(three_trades))
    monkeypatch.setattr(dashboard, "_load_open_positions", _async_return(open_positions))

    payload = await get_dashboard_performance(FakeDB(), "user-1")
    # current_equity = cash 100900 + open market value (10*100 + 5*200 = 2000).
    assert payload["current_equity"] == pytest.approx(102900.0)
    assert payload["total_pnl"] == pytest.approx(2900.0)
    assert payload["open_positions"] == 2


@pytest.mark.asyncio
async def test_get_dashboard_performance_no_account(monkeypatch):
    """Missing paper account still returns sensible defaults."""
    class FakeDB:
        pass

    monkeypatch.setattr(dashboard, "_load_account", _async_return(None))
    monkeypatch.setattr(dashboard, "_load_closed_positions", _async_return([]))
    monkeypatch.setattr(dashboard, "_load_open_positions", _async_return([]))

    payload = await get_dashboard_performance(FakeDB(), "user-1")
    assert payload["starting_balance"] == 100000.0
    assert payload["current_equity"] == 100000.0
    assert payload["total_trades_closed"] == 0


def _async_return(value):
    """Return an async function yielding ``value`` for monkeypatching."""
    async def _fake(*args, **kwargs):
        return value
    return _fake
