"""Strategy engine — load strategies from DB, fetch market data, and run them."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy import Strategy
from app.services.market_data import get_daily_bars
from app.strategies import BaseStrategy, StrategySignal, get_strategy
from app.strategies.indicators import sma

logger = logging.getLogger(__name__)

#: Market index symbol used by the "spy200sma" regime filter.
REGIME_SYMBOL = "SPY"
#: SMA period for the "spy200sma" regime filter.
REGIME_SMA_PERIOD = 200


def _min_bars_to_lookback_days(min_bars: int) -> int:
    """Convert a required number of daily bars into calendar days.

    ~252 trading days fall in ~365 calendar days; a 10% safety buffer
    guarantees the fetched window contains at least *min_bars* daily bars
    even across holidays and partial weeks.
    """
    return int(math.ceil(min_bars * (365.0 / 252.0) * 1.10))


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
    regime_filter = getattr(db_strategy, "regime_filter", None) or None

    # 2. Instantiate the strategy class via registry
    try:
        strategy: BaseStrategy = get_strategy(strategy_name, config=config)
    except KeyError:
        logger.error(
            "Strategy '%s' is in the DB but not registered in STRATEGY_REGISTRY.",
            strategy_name,
        )
        return []

    # 3. Date range — a strategy with a long indicator window (e.g. the
    # 200-day SMA of ``momentum_pullback``) needs more history than the
    # default lookback. Always honor at least the strategy's ``min_bars``.
    end_date = datetime.now(timezone.utc)
    required_lookback = _min_bars_to_lookback_days(strategy.min_bars)
    lookback_days = max(lookback_days, required_lookback)
    start_date = end_date - timedelta(days=lookback_days)

    # 4. Regime filter — when enabled, fetch the index's bars once so the
    # gate can be evaluated for every symbol in this scan. SPY needs at
    # least REGIME_SMA_PERIOD daily bars for a defined SMA, so extend the
    # lookback for this fetch beyond the strategy's own requirement
    # (mirrors production, where the indicator always has full history).
    spy_close: float | None = None
    spy_sma200: float | None = None
    if regime_filter == "spy200sma":
        spy_lookback = max(lookback_days, _min_bars_to_lookback_days(REGIME_SMA_PERIOD))
        spy_start = end_date - timedelta(days=spy_lookback)
        try:
            spy_bars = await get_daily_bars(db, REGIME_SYMBOL, spy_start, end_date)
        except Exception as exc:
            logger.exception("Failed to fetch %s bars for regime filter.", REGIME_SYMBOL)
            spy_bars = []
        if len(spy_bars) >= REGIME_SMA_PERIOD:
            closes = [float(b.close) for b in spy_bars]
            sma_line = sma(closes, REGIME_SMA_PERIOD)
            last_sma = sma_line[-1]
            if last_sma is not None:
                spy_close = closes[-1]
                spy_sma200 = float(last_sma)
                logger.info(
                    "Regime filter 'spy200sma': %s close %.2f vs SMA%d %.2f",
                    REGIME_SYMBOL, spy_close, REGIME_SMA_PERIOD, spy_sma200,
                )
            else:
                logger.warning(
                    "Regime filter 'spy200sma': %s SMA%d undefined on latest bar — signals will be skipped.",
                    REGIME_SYMBOL, REGIME_SMA_PERIOD,
                )
        else:
            logger.warning(
                "Regime filter 'spy200sma': only %d %s bars available (< %d) — signals will be skipped.",
                len(spy_bars), REGIME_SYMBOL, REGIME_SMA_PERIOD,
            )

    # 5. Analyze each symbol
    signals: list[StrategySignal] = []
    filtered = 0
    for symbol in symbols:
        try:
            bars = await get_daily_bars(db, symbol, start_date, end_date)
            if not bars:
                logger.debug("No bars returned for %s — skipping.", symbol)
                continue

            signal = await strategy.analyze(symbol, bars)
            if signal is not None and regime_filter == "spy200sma":
                # Skip the signal when SPY is at or below its 200-day SMA:
                # the validated regime filter only trades above it.
                if spy_close is None or spy_sma200 is None:
                    logger.warning(
                        "Regime filter 'spy200sma' unavailable — skipping %s signal for %s.",
                        signal.action.upper(), signal.symbol,
                    )
                    filtered += 1
                    continue
                if spy_close <= spy_sma200:
                    logger.info(
                        "Regime filter 'spy200sma': %s %.2f <= SMA%d %.2f — %s signal for %s filtered.",
                        REGIME_SYMBOL, spy_close, REGIME_SMA_PERIOD, spy_sma200,
                        signal.action.upper(), signal.symbol,
                    )
                    filtered += 1
                    continue
            if signal is not None:
                signals.append(signal)
                logger.info(
                    "Strategy '%s' → %s %s (confidence: %.1f)",
                    strategy_name,
                    signal.action.upper(),
                    signal.symbol,
                    signal.confidence,
                )
        except Exception as exc:
            logger.exception("Error analyzing %s with strategy '%s'.", symbol, strategy_name)
            signals.append(
                StrategySignal(
                    symbol=symbol,
                    action="hold",
                    confidence=0,
                    error=str(exc),
                )
            )

    if filtered:
        logger.info("Strategy '%s': %d signal(s) filtered by regime filter '%s'.", strategy_name, filtered, regime_filter)

    return signals
