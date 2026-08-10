"""Simple trend-breakout strategy.

Enters when price is on the correct side of its long-term trend and breaks
its recent range.  The deliberately small two-condition gate keeps this
strategy focused on momentum rather than indicator stacking.
"""

from __future__ import annotations

from app.schemas.market_data import Bar
from app.strategies import BaseStrategy, StrategySignal, register_strategy
from app.strategies.indicators import atr, sma


@register_strategy
class BreakoutStrategy(BaseStrategy):
    """Trade 20-bar breakouts in the direction of the 200-bar trend."""

    name = "breakout"
    display_name = "Trend Breakout"
    min_bars = 300

    DEFAULTS: dict = {
        "trend_period": 200,
        "breakout_period": 20,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 4.0,
        "min_bars": 300,
    }

    def _cfg(self, key: str):
        return self.config.get(key, self.DEFAULTS.get(key))

    @staticmethod
    def _last(values: list[float | None]) -> float | None:
        for value in reversed(values):
            if value is not None:
                return value
        return None

    async def analyze(self, symbol: str, bars: list[Bar]) -> StrategySignal | None:
        if len(bars) < self._cfg("min_bars"):
            return None

        closes = [float(bar.close) for bar in bars]
        highs = [float(bar.high) for bar in bars]
        lows = [float(bar.low) for bar in bars]
        trend_period = self._cfg("trend_period")
        breakout_period = self._cfg("breakout_period")

        latest_close = closes[-1]
        latest_sma = self._last(sma(closes, trend_period))
        latest_atr = self._last(atr(highs, lows, closes, self._cfg("atr_period")))
        # Compare with the completed lookback window; including the current
        # bar would make every close equal to a max/min by definition.
        prior_window = closes[-breakout_period - 1 : -1]
        if latest_sma is None or latest_atr is None or len(prior_window) < breakout_period:
            return None
        recent_high = max(prior_window)
        recent_low = min(prior_window)

        long_trend = latest_close > latest_sma
        short_trend = latest_close < latest_sma
        long_breakout = latest_close >= recent_high
        short_breakout = latest_close <= recent_low
        indicators = {
            "sma_200": latest_sma,
            "sma": latest_sma,
            "recent_high_20": recent_high,
            "recent_low_20": recent_low,
            "atr": latest_atr,
            "latest_close": latest_close,
            "conditions": {
                "above_sma_200": long_trend,
                "new_20_day_high": long_breakout,
                "below_sma_200": short_trend,
                "new_20_day_low": short_breakout,
            },
        }

        if long_trend and long_breakout:
            action = "buy"
            reasoning = "New 20-day high above 200-SMA: breakout signal"
        elif short_trend and short_breakout:
            action = "sell"
            reasoning = "New 20-day low below 200-SMA: breakout signal"
        else:
            return None

        stop_mult = self._cfg("atr_stop_mult")
        target_mult = self._cfg("atr_target_mult")
        if action == "buy":
            stop_loss = latest_close - stop_mult * latest_atr
            take_profit = latest_close + target_mult * latest_atr
        else:
            stop_loss = latest_close + stop_mult * latest_atr
            take_profit = latest_close - target_mult * latest_atr

        return StrategySignal(
            symbol=symbol,
            action=action,
            confidence=100.0,
            entry_price=latest_close,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=reasoning,
            indicators=indicators,
        )
