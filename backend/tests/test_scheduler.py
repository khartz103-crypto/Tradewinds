"""Tests for the scheduled auto-trading scanner."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.services import scheduler


class _FakeRedis:
    """In-memory Redis stand-in for scheduler state tests."""

    def __init__(self):
        self._data = {}

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value):
        self._data[key] = value

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    """Point the scheduler at an in-memory Redis."""
    from app.services import redis_client

    redis = _FakeRedis()
    monkeypatch.setattr(redis_client, "_redis", redis)
    return redis


async def test_scheduler_defaults_to_disabled(fake_redis):
    assert await scheduler.is_enabled() is False


async def test_scheduler_enable_disable_roundtrip(fake_redis):
    await scheduler.set_enabled(True)
    assert await scheduler.is_enabled() is True
    await scheduler.set_enabled(False)
    assert await scheduler.is_enabled() is False


async def test_scheduler_status_shape(fake_redis):
    status = await scheduler.get_status()
    assert status["running"] is False
    assert status["interval_seconds"] == 15 * 60
    assert status["default_symbols"] == scheduler.DEFAULT_SYMBOLS
    assert len(status["default_symbols"]) == 25
    assert status["last_run"] is None
    assert status["last_summary"] is None


async def test_scheduler_status_reflects_enabled(fake_redis):
    await scheduler.set_enabled(True)
    status = await scheduler.get_status()
    assert status["running"] is True


async def test_run_once_opens_positions_for_admin(monkeypatch, fake_redis):
    """run_once scans the default watchlist and auto-trades for the admin."""

    class FakeUser:
        id = uuid4()
        is_admin = True

    class FakeStrategy:
        id = uuid4()

    class FakeSession:
        """A bare session whose execute() returns scripted results."""

        def __init__(self):
            self._script = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def add_script(self, value):
            self._script.append(value)

        async def execute(self, *args, **kwargs):
            return self._script.pop(0)

        async def commit(self):
            self.committed = True

    session = FakeSession()
    session.add_script(type("R", (), {"scalar_one_or_none": lambda self: FakeUser()})())
    session.add_script(type("R", (), {"scalar_one_or_none": lambda self: FakeStrategy()})())

    async def fake_run_strategy(db, name, symbols):
        assert name == "trend_following"
        assert symbols == scheduler.DEFAULT_SYMBOLS
        return [_signal("AAPL"), _signal("MSFT")]

    async def fake_auto_trade(db, user_id, signals, **kwargs):
        return [
            {"symbol": "AAPL", "side": "long", "error": None},
            {"symbol": "MSFT", "side": "long", "error": "Already has an open position"},
        ]

    # run_once imports its dependencies lazily, so patch the source modules.
    import app.database
    import app.services.auto_trade
    import app.services.strategy_engine

    monkeypatch.setattr(app.database, "async_session", lambda: session)
    monkeypatch.setattr(app.services.strategy_engine, "run_strategy", fake_run_strategy)
    monkeypatch.setattr(app.services.auto_trade, "auto_trade_signals", fake_auto_trade)

    summary = await scheduler.run_once()
    assert session.committed is True
    assert summary == {
        "scanned_symbols": 25,
        "signals": 2,
        "opened": 1,
        "skipped": 1,
    }

    # Run details are persisted to Redis for the status endpoint.
    await scheduler._record_run(summary)
    status = await scheduler.get_status()
    assert status["last_run"] is not None
    assert status["last_summary"]["opened"] == 1


def _signal(symbol: str):
    from app.strategies import StrategySignal

    return StrategySignal(
        symbol=symbol,
        action="buy",  # type: ignore[arg-type]
        confidence=70.0,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        reasoning="test",
    )


async def test_loop_is_idempotent(monkeypatch, fake_redis):
    """start_loop twice must not spawn duplicate background tasks."""
    monkeypatch.setattr(scheduler, "INTERVAL_SECONDS", 3600)

    task1 = scheduler.start_loop()
    task2 = scheduler.start_loop()
    assert task1 is task2
    assert scheduler._loop_task is task1

    await scheduler.stop_loop()
    assert scheduler._loop_task is None
    assert task1.cancelled() or task1.done()


async def test_loop_sleeps_when_disabled(monkeypatch, fake_redis):
    """While disabled, the loop must not run a scan."""
    ran = []
    sleep_calls = []

    async def fake_is_enabled():
        return False

    async def fake_run_once():
        ran.append(True)

    original_sleep = asyncio.sleep

    async def counting_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 3:
            await original_sleep(3600)  # pause after a few disabled iterations

    monkeypatch.setattr(scheduler, "INTERVAL_SECONDS", 900)
    monkeypatch.setattr(scheduler, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(asyncio, "sleep", counting_sleep)

    task = scheduler.start_loop()
    await original_sleep(0.2)  # let the loop iterate a few times
    await scheduler.stop_loop()

    assert task.done() or task.cancelled()
    assert ran == []
    assert len(sleep_calls) >= 3


async def test_loop_runs_once_when_enabled(monkeypatch, fake_redis):
    """When enabled, the loop calls run_once and records the run."""
    calls = []
    first_tick = asyncio.Event()

    async def fake_is_enabled():
        return True

    async def fake_run_once():
        calls.append(1)
        return {"opened": 1}

    async def fake_record_run(summary):
        calls.append(summary)
        first_tick.set()

    original_sleep = asyncio.sleep

    async def short_sleep(seconds):
        await original_sleep(0.02)

    monkeypatch.setattr(scheduler, "is_enabled", fake_is_enabled)
    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler, "_record_run", fake_record_run)
    monkeypatch.setattr(asyncio, "sleep", short_sleep)

    task = scheduler.start_loop()
    await asyncio.wait_for(first_tick.wait(), timeout=5)
    await scheduler.stop_loop()

    assert task.done() or task.cancelled()
    assert calls.count(1) >= 1
    assert {"opened": 1} in calls
