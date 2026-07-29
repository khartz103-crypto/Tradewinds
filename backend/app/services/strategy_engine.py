"""Strategy engine — load strategies from DB, fetch market data, and run them."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import Strategy
from app.services.market_data import get_daily_bars
from app.strategies import BaseStrategy, StrategySignal, get_strategy

logger = logging.getLogger(__name__)


async def run_strategy(
    db: AsyncSession,
    strategy_name: str,
    symbols: list[str],
    *,
    lookback_days: int = 120,
) -> list[StrategySignal]:
    """Load a named strategy from the DB, fetch bars for each symbol, and
    return all generated signals.

    Args:
        db: An active async DB session.
        strategy_name: Name of the registered strategy (e.g. ``"trend_following"``).
        symbols: List of ticker symbols to scan.
        lookback_days: How many calendar days of daily bars to fetch.

    Returns:
        A list of ``StrategySignal`` objects (one per symbol that triggered).
    """
    # 1. Load strategy config from DB
    result = await db.execute(
        select(Strategy).where(
            Strategy.name == strategy_name,
            Strategy.is_enabled == True,  # noqa: E712
        )
    )
    db_strategy = result.scalar_one_or_none()
    if db_strategy is None:
        logger.warning("Strategy '%s' not found or disabled.", strategy_name)
        return []

    config = db_strategy.config or {}

    # 2. Instantiate the strategy class via registry
    try:
        strategy: BaseStrategy = get_strategy(strategy_name, config=config)
    except KeyError:
        logger.error(
            "Strategy '%s' is in the DB but not registered in STRATEGY_REGISTRY.",
            strategy_name,
        )
        return []

    # 3. Date range
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=lookback_days)

    # 4. Analyze each symbol
    signals: list[StrategySignal] = []
    for symbol in symbols:
        try:
            bars = await get_daily_bars(db, symbol, start_date, end_date)
            if not bars:
                logger.debug("No bars returned for %s — skipping.", symbol)
                continue

            signal = await strategy.analyze(symbol, bars)
            if signal is not None:
                signals.append(signal)
                logger.info(
                    "Strategy '%s' → %s %s (confidence: %.1f)",
                    strategy_name,
                    signal.action.upper(),
                    signal.symbol,
                    signal.confidence,
                )
        except Exception:
            logger.exception("Error analyzing %s with strategy '%s'.", symbol, strategy_name)

    return signals
