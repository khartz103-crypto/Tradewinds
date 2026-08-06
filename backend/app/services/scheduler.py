"""Scheduled auto-trading scanner.

A lightweight background task that re-runs the market scanner against the
default watchlist every 15 minutes and auto-opens paper positions via the
paper trading engine — no user interaction required.

The enabled/disabled state lives in Redis so it survives backend restarts:
on startup the loop is always launched, and it simply checks Redis before
each tick. If Redis is unreachable the loop treats itself as disabled and
keeps retrying.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.strategy import Strategy
from app.models.user import User

logger = logging.getLogger(__name__)

#: Default watchlist scanned on every scheduled tick (same as the scanner UI).
DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "NFLX", "JPM", "V",
]

#: Strategy used by the scheduled scanner.
DEFAULT_STRATEGY = "trend_following"

#: How often the scheduled scanner runs.
INTERVAL_SECONDS = 15 * 60

# Redis keys
KEY_ENABLED = "scheduler:auto_trade:enabled"
KEY_LAST_RUN = "scheduler:auto_trade:last_run"
KEY_LAST_SUMMARY = "scheduler:auto_trade:last_summary"

#: Module-level background task so start/stop are idempotent.
_loop_task: asyncio.Task | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── state (Redis) ───────────────────────────────────────────────────────


async def is_enabled() -> bool:
    """Return whether the scheduled scanner is currently enabled."""
    from app.services.redis_client import get_redis

    try:
        value = await get_redis().get(KEY_ENABLED)
        return value == "1"
    except Exception as exc:  # Redis unreachable → stay off
        logger.warning("Scheduler state check failed: %s", exc)
        return False


async def set_enabled(enabled: bool) -> None:
    """Persist the enabled flag to Redis."""
    from app.services.redis_client import get_redis

    await get_redis().set(KEY_ENABLED, "1" if enabled else "0")

async def get_status() -> dict:
    """Return scheduler status for the API / frontend."""
    from app.services.redis_client import get_redis

    redis = get_redis()
    running = await is_enabled()
    try:
        last_run = await redis.get(KEY_LAST_RUN)
        last_summary_raw = await redis.get(KEY_LAST_SUMMARY)
    except Exception:
        last_run = None
        last_summary_raw = None

    last_summary = None
    if last_summary_raw:
        try:
            last_summary = json.loads(last_summary_raw)
        except json.JSONDecodeError:
            last_summary = None

    return {
        "running": running,
        "interval_seconds": INTERVAL_SECONDS,
        "default_symbols": DEFAULT_SYMBOLS,
        "last_run": last_run,
        "last_summary": last_summary,
    }


async def _record_run(summary: dict) -> None:
    """Persist the last-run timestamp and summary to Redis."""
    from app.services.redis_client import get_redis

    try:
        redis = get_redis()
        await redis.set(KEY_LAST_RUN, _now_iso())
        await redis.set(KEY_LAST_SUMMARY, json.dumps(summary))
    except Exception as exc:
        logger.warning("Failed to record scheduler run: %s", exc)


# ── scan + auto-trade pass ──────────────────────────────────────────────


async def run_once() -> dict:
    """Run one scheduled scan + auto-trade pass against the default watchlist."""
    from app.database import async_session
    from app.services.auto_trade import auto_trade_signals
    from app.services.position_manager import manage_positions
    from app.services.strategy_engine import run_strategy

    # A fresh session per cycle prevents a failed flush from poisoning the next cycle.
    async with async_session() as db:
        try:
            result = await db.execute(select(User).where(User.is_admin == True).order_by(User.created_at).limit(1))  # noqa: E712
            admin = result.scalar_one_or_none()
            if admin is None:
                logger.warning("Scheduler tick skipped — no admin user found")
                return {"scanned": False, "reason": "no admin user"}
            management = await manage_positions(db, user_id=admin.id)
            signals, results = [], []
            for strategy_name in ("trend_following", "mean_reversion"):
                strategy_result = await db.execute(select(Strategy).where(Strategy.name == strategy_name))
                strategy = strategy_result.scalar_one_or_none()
                strategy_signals = await run_strategy(db, strategy_name, DEFAULT_SYMBOLS)
                signals.extend(strategy_signals)
                results.extend(await auto_trade_signals(
                    db, user_id=admin.id, signals=strategy_signals,
                    strategy_id=strategy.id if strategy else None,
                    risk_per_trade=(strategy.config or {}).get("risk_per_trade", 0.005) if strategy else 0.005,
                    strategy_name=strategy_name,
                ))
            await db.commit()
            summary = {
                "position_management": management,
                "scanned_symbols": len(DEFAULT_SYMBOLS) * 2,
                "signals": len(signals),
                "opened": sum(1 for r in results if r.get("error") is None),
                "skipped": sum(1 for r in results if r.get("error") is not None),
                "errors": {
                    error: sum(1 for r in results if r.get("error") == error)
                    for error in {r.get("error") for r in results if r.get("error") is not None}
                },
            }
            logger.info("Scheduled scan complete: %s", summary)
            return summary
        except Exception:
            logger.exception("Scheduler cycle failed — DB error details:")
            try:
                await db.rollback()
            except Exception:
                logger.exception("Failed to rollback scheduler session")
            raise

# ── background loop ─────────────────────────────────────────────────────


async def _loop() -> None:
    """Background loop: run the scan while enabled, then sleep."""
    first_tick = True
    while True:
        try:
            if first_tick:
                logger.info("Scheduler starting first cycle")
                first_tick = False
            if await is_enabled():
                # Record the start immediately so status reflects an in-flight cycle.
                await _record_run({"scanned": False, "status": "running"})
                summary = await run_once()
                await _record_run(summary)
        except Exception as exc:
            logger.exception("Scheduler tick failed")
            await _record_run({"error": str(exc)[:1000], "scanned": False})
        await asyncio.sleep(INTERVAL_SECONDS)


def start_loop() -> asyncio.Task:
    """Launch the background scheduler loop (idempotent)."""
    global _loop_task
    if _loop_task is None or _loop_task.done():
        _loop_task = asyncio.create_task(_loop(), name="auto-trade-scheduler")
        logger.info("Auto-trade scheduler loop started (interval=%ss)", INTERVAL_SECONDS)
    return _loop_task


async def stop_loop() -> None:
    """Cancel the background scheduler loop (idempotent)."""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass
        logger.info("Auto-trade scheduler loop stopped")
    _loop_task = None
