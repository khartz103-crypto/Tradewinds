"""Tests for the auto-trade pipeline (scanner signals → paper positions)."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.services import auto_trade
from app.strategies import StrategySignal


def _signal(
    symbol: str,
    action: str = "buy",
    entry_price: float = 100.0,
    stop_loss: float | None = 95.0,
    take_profit: float | None = 115.0,
    error: str | None = None,
) -> StrategySignal:
    return StrategySignal(
        symbol=symbol,
        action=action,  # type: ignore[arg-type]
        confidence=70.0,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        reasoning="test",
        error=error,
    )


class _FakePosition:
    """Minimal stand-in for a Position ORM object."""

    def __init__(self, symbol: str, side: str, qty: Decimal, price: Decimal):
        self.id = uuid4()
        self.symbol = symbol
        self.side = type("Side", (), {"value": side})()
        self.quantity = qty
        self.entry_price = price
        self.stop_loss = None
        self.take_profit = None


@pytest.fixture
def fake_db(monkeypatch):
    """Patch DB-touching helpers so tests never hit a database."""
    class DB:
        pass

    db = DB()
    monkeypatch.setattr(auto_trade, "get_open_symbols", _fake_get_open_symbols)
    monkeypatch.setattr(auto_trade, "get_cash_balance", _fake_get_cash_balance)

    async def fake_open_position(**kwargs):
        return _FakePosition(
            symbol=kwargs["symbol"],
            side=kwargs["action"],
            qty=kwargs["qty"],
            price=kwargs["entry_price"],
        )

    monkeypatch.setattr(auto_trade, "open_position", fake_open_position)
    return db


async def _fake_get_open_symbols(db, user_id):
    return {"AAPL"}  # AAPL already has an open position


async def _fake_get_cash_balance(db, user_id):
    return Decimal("100000.00")


async def test_skips_symbols_with_open_positions(fake_db):
    """Symbols with an existing open position must not be doubled up."""
    signals = [
        _signal("AAPL"),  # already open
        _signal("MSFT"),
    ]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert len(results) == 2
    assert results[0]["symbol"] == "AAPL"
    assert results[0]["error"] == "Already has an open position"
    assert results[1]["symbol"] == "MSFT"
    assert results[1]["error"] is None


async def test_skips_error_and_hold_signals(fake_db):
    """Signals with errors or 'hold' actions are not traded."""
    signals = [
        _signal("TSLA", error="boom"),
        _signal("GOOGL", action="hold"),
        _signal("NVDA"),
    ]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert len(results) == 1
    assert results[0]["symbol"] == "NVDA"
    assert results[0]["error"] is None


async def test_default_position_size_is_risk_based(fake_db):
    """With no position_size, each trade sizes risk at 0.5% of the cash balance."""
    signals = [_signal("MSFT", entry_price=100.0)]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert results[0]["error"] is None
    # 0.5% of 100,000 = 500 risk dollars / $5 stop distance = 100 shares
    assert results[0]["quantity"] == Decimal("100.0000")
    assert results[0]["side"] == "long"


async def test_explicit_position_size(fake_db):
    """An explicit position_size overrides the 10% default."""
    signals = [_signal("MSFT", action="sell", entry_price=50.0)]
    results = await auto_trade.auto_trade_signals(
        fake_db, uuid4(), signals, position_size=2500.0
    )
    assert results[0]["error"] is None
    assert results[0]["quantity"] == Decimal("50.0000")
    assert results[0]["side"] == "short"


async def test_quantity_respects_buying_power(fake_db):
    """Position size must never exceed the cash balance."""
    signals = [_signal("MSFT", entry_price=10.0)]
    results = await auto_trade.auto_trade_signals(
        fake_db, uuid4(), signals, position_size=10_000_000.0
    )
    # Capped at the full 100k balance → 10,000 shares @ $10
    assert results[0]["quantity"] == Decimal("10000.0000")


async def test_no_valid_entry_price_is_reported(fake_db):
    """Signals without an entry price are reported, not opened."""
    signals = [_signal("F", entry_price=None)]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert results[0]["error"] == "No valid entry price in signal"


async def test_open_position_failure_is_reported(fake_db, monkeypatch):
    """Risk-limit rejections surface per-symbol without stopping the batch."""

    async def failing_open_position(**kwargs):
        raise ValueError("at max open positions (5)")

    monkeypatch.setattr(auto_trade, "open_position", failing_open_position)
    signals = [_signal("MSFT"), _signal("NVDA")]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert len(results) == 2
    assert all(r["error"] == "at max open positions (5)" for r in results)


async def test_no_doubling_within_batch(fake_db):
    """Two signals for the same symbol open only one position."""
    signals = [
        _signal("MSFT", action="buy"),
        _signal("MSFT", action="sell"),
    ]
    results = await auto_trade.auto_trade_signals(fake_db, uuid4(), signals)
    assert results[0]["error"] is None
    assert results[1]["error"] == "Already has an open position"
