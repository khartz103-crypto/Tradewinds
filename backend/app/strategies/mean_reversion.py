"""Mean-reversion strategy for range-bound and choppy markets."""

from __future__ import annotations

from app.schemas.market_data import Bar
from app.strategies import BaseStrategy, StrategySignal, register_strategy
from app.strategies.indicators import atr, bbands, rsi, sma


@register_strategy
class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    display_name = "Mean Reversion"

    DEFAULTS: dict = {
        "rsi_period": 14,
        "rsi_oversold": 30,
        "rsi_overbought": 70,
        "bb_period": 20,
        "bb_std": 2.0,
        "min_signals": 2,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 3.0,
        "distance_threshold": 0.02,
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
        period = max(self._cfg("rsi_period"), self._cfg("bb_period"), self._cfg("atr_period"))
        if len(bars) < period + 1:
            return None
        closes = [float(bar.close) for bar in bars]
        highs = [float(bar.high) for bar in bars]
        lows = [float(bar.low) for bar in bars]
        latest_close = closes[-1]
        latest_rsi = self._last(rsi(closes, self._cfg("rsi_period")))
        lower, middle, upper = bbands(closes, self._cfg("bb_period"), self._cfg("bb_std"))
        latest_lower, latest_middle, latest_upper = self._last(lower), self._last(middle), self._last(upper)
        latest_atr = self._last(atr(highs, lows, closes, self._cfg("atr_period")))
        if any(value is None for value in (latest_rsi, latest_lower, latest_middle, latest_upper, latest_atr)):
            return None

        distance = abs(latest_close - latest_middle) / latest_middle if latest_middle else 0.0
        bullish = [latest_rsi < self._cfg("rsi_oversold"), latest_close <= latest_lower,
                   distance > self._cfg("distance_threshold") and latest_close < latest_middle]
        bearish = [latest_rsi > self._cfg("rsi_overbought"), latest_close >= latest_upper,
                   distance > self._cfg("distance_threshold") and latest_close > latest_middle]
        bullish_count, bearish_count = sum(bullish), sum(bearish)
        min_signals = self._cfg("min_signals")
        if bullish_count >= min_signals:
            action, count = "buy", bullish_count
        elif bearish_count >= min_signals:
            action, count = "sell", bearish_count
        else:
            return None
        stop_mult, target_mult = self._cfg("atr_stop_mult"), self._cfg("atr_target_mult")
        if action == "buy":
            stop_loss, take_profit = latest_close - stop_mult * latest_atr, latest_close + target_mult * latest_atr
        else:
            stop_loss, take_profit = latest_close + stop_mult * latest_atr, latest_close - target_mult * latest_atr
        confidence = round(count / 3 * 100, 1)
        return StrategySignal(
            symbol=symbol, action=action, confidence=confidence,
            entry_price=latest_close, stop_loss=round(stop_loss, 2), take_profit=round(take_profit, 2),
            reasoning=f"{action.upper()} signal for {symbol} (Mean Reversion): {count}/3 conditions met. Confidence: {confidence:.1f}%",
            indicators={"rsi": latest_rsi, "bb_lower": latest_lower, "bb_middle": latest_middle,
                        "bb_upper": latest_upper, "distance": distance, "atr": latest_atr},
        )
