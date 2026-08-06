"""Momentum pullback strategy — enter pullbacks inside a confirmed trend.

Improves on ``trend_following`` by not chasing momentum: it waits for a
strong uptrend (50-day SMA above the 200-day SMA) to pull back 3–10% from a
recent high, touch the 20-day SMA / lower Bollinger Band, and show the first
sign of stabilization (close ticks up). Entry is therefore at a better price
than a momentum chase, while the ATR-based stop/target keep the risk/reward
at a fixed 1:2 (2×ATR stop, 4×ATR target).

A mirror implementation trades the short side of a confirmed downtrend.
"""

from __future__ import annotations

from app.schemas.market_data import Bar
from app.strategies import BaseStrategy, StrategySignal, register_strategy
from app.strategies.indicators import atr, bbands, sma


@register_strategy
class MomentumPullbackStrategy(BaseStrategy):
    """Buy pullbacks in strong uptrends (and short pullbacks in downtrends).

    Every condition is an AND gate — a signal only fires when *all* of the
    following hold for the long side (mirrored for the short side):

    1. ``sma(trend_fast_period) > sma(trend_slow_period)`` — confirmed uptrend.
    2. Price sits 3–10% below the highest high of the last
       ``recent_high_period`` bars (pullback zone).
    3. Price has touched or crossed the 20-day SMA or the lower Bollinger Band
       (pullback depth).
    4. ATR is positive and available.
    5. Current close > prior close (stabilization / bounce starting).

    Config keys (all optional, defaults shown):
        trend_fast_period: int   = 50    Fast SMA for trend confirmation
        trend_slow_period: int   = 200   Slow SMA for trend confirmation
        recent_high_period: int  = 60    Lookback for the recent high/low
        pullback_min_pct: float  = 0.03  Min distance from recent extreme
        pullback_max_pct: float  = 0.10  Max distance from recent extreme
        bollinger_period: int    = 20    SMA / Bollinger period
        bollinger_std: float     = 2.0   Bollinger band width (std devs)
        atr_period: int          = 14    ATR period
        atr_stop_mult: float     = 2.0   Stop-loss = mult × ATR from entry
        atr_target_mult: float   = 4.0   Take-profit = mult × ATR from entry
    """

    name = "momentum_pullback"
    display_name = "Momentum Pullback"

    #: The 200-day SMA needs ~200 bars of history; add room for the 60-day
    #: recent-high window and indicator warmup. This also drives the scanner
    #: lookback and the backtest warmup (see strategy_engine / backtest).
    min_bars = 400

    DEFAULTS: dict = {
        "trend_fast_period": 50,
        "trend_slow_period": 200,
        "recent_high_period": 60,
        "pullback_min_pct": 0.03,
        "pullback_max_pct": 0.10,
        "bollinger_period": 20,
        "bollinger_std": 2.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 4.0,
    }

    # ── helpers ───────────────────────────────────────────────────────

    def _cfg(self, key: str):
        """Return config value or default."""
        return self.config.get(key, self.DEFAULTS.get(key))

    @staticmethod
    def _last(values: list[float | None]) -> float | None:
        """Return the rightmost non-None value or None."""
        for v in reversed(values):
            if v is not None:
                return v
        return None

    # ── analyze ───────────────────────────────────────────────────────

    async def analyze(
        self, symbol: str, bars: list[Bar]
    ) -> StrategySignal | None:
        """Evaluate bars and return a BUY / SELL signal or None."""
        if len(bars) < self.min_bars:
            return None

        closes = [float(b.close) for b in bars]
        highs = [float(b.high) for b in bars]
        lows = [float(b.low) for b in bars]

        fast = self._cfg("trend_fast_period")
        slow = self._cfg("trend_slow_period")
        recent_period = self._cfg("recent_high_period")
        bb_period = self._cfg("bollinger_period")
        bb_std = self._cfg("bollinger_std")
        atr_period = self._cfg("atr_period")

        # --- compute indicators --------------------------------------
        sma_fast_line = sma(closes, fast)
        sma_slow_line = sma(closes, slow)
        bb_lower_line, bb_mid_line, bb_upper_line = bbands(
            closes, bb_period, bb_std
        )
        atr_line = atr(highs, lows, closes, atr_period)

        latest_close = closes[-1]
        prev_close = closes[-2]
        recent_high = max(highs[-recent_period:])
        recent_low = min(lows[-recent_period:])
        latest_sma_fast = self._last(sma_fast_line)
        latest_sma_slow = self._last(sma_slow_line)
        latest_bb_lower = self._last(bb_lower_line)
        latest_bb_mid = self._last(bb_mid_line)
        latest_bb_upper = self._last(bb_upper_line)
        latest_atr = self._last(atr_line)

        pullback_pct = (
            (recent_high - latest_close) / recent_high if recent_high > 0 else 0.0
        )
        rally_pct = (
            (latest_close - recent_low) / recent_low if recent_low > 0 else 0.0
        )

        indicators = {
            "sma_fast": latest_sma_fast,
            "sma_slow": latest_sma_slow,
            "sma_mid": latest_bb_mid,
            "bb_lower": latest_bb_lower,
            "bb_upper": latest_bb_upper,
            "atr": latest_atr,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "pullback_pct": round(pullback_pct, 4),
            "rally_pct": round(rally_pct, 4),
            "latest_close": latest_close,
            "prev_close": prev_close,
        }

        # Any indicator missing → cannot evaluate, no signal.
        if any(
            v is None
            for v in (
                latest_sma_fast,
                latest_sma_slow,
                latest_bb_lower,
                latest_bb_upper,
                latest_atr,
            )
        ):
            return None

        # --- evaluate conditions (long) ------------------------------
        pullback_min = self._cfg("pullback_min_pct")
        pullback_max = self._cfg("pullback_max_pct")

        long_conditions = {
            "trend_established": bool(latest_sma_fast > latest_sma_slow),  # type: ignore[operator]
            "pullback_zone": bool(pullback_min <= pullback_pct <= pullback_max),
            "pullback_depth": bool(
                latest_close <= latest_bb_mid or latest_close <= latest_bb_lower  # type: ignore[operator]
            ),
            "atr_positive": bool(latest_atr is not None and latest_atr > 0),
            "stabilization": bool(latest_close > prev_close),
        }

        short_conditions = {
            "trend_established": bool(latest_sma_fast < latest_sma_slow),  # type: ignore[operator]
            "pullback_zone": bool(pullback_min <= rally_pct <= pullback_max),
            "pullback_depth": bool(
                latest_close >= latest_bb_mid or latest_close >= latest_bb_upper  # type: ignore[operator]
            ),
            "atr_positive": bool(latest_atr is not None and latest_atr > 0),
            "stabilization": bool(latest_close < prev_close),
        }

        # --- AND gate: every condition must pass in one direction -----
        if all(long_conditions.values()):
            action = "buy"
            conditions = long_conditions
        elif all(short_conditions.values()):
            action = "sell"
            conditions = short_conditions
        else:
            # No signal — still return a hold-style diagnostic? No: contract
            # says ``None`` when no signal. Conditions go nowhere.
            return None

        # --- signal levels -------------------------------------------
        entry_price = latest_close
        stop_mult = self._cfg("atr_stop_mult")
        target_mult = self._cfg("atr_target_mult")
        if action == "buy":
            stop_loss = entry_price - stop_mult * latest_atr  # type: ignore[operator]
            take_profit = entry_price + target_mult * latest_atr  # type: ignore[operator]
        else:
            stop_loss = entry_price + stop_mult * latest_atr  # type: ignore[operator]
            take_profit = entry_price - target_mult * latest_atr  # type: ignore[operator]

        # --- confidence: fraction of conditions met (all 5 on a fire) --
        confidence = round(
            (sum(1 for v in conditions.values() if v) / len(conditions)) * 100.0, 1
        )

        condition_detail = ", ".join(
            f"{name}={('PASS' if result else 'FAIL')}"
            for name, result in conditions.items()
        )
        reasoning = (
            f"{action.upper()} signal for {symbol} ({self.display_name}). "
            f"All {len(conditions)}/5 conditions met: [{condition_detail}]. "
            f"Entry: {entry_price:.2f}, SL: {stop_loss:.2f}, TP: {take_profit:.2f}. "
            f"Confidence: {confidence:.1f}%"
        )

        return StrategySignal(
            symbol=symbol,
            action=action,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            reasoning=reasoning,
            indicators={**indicators, "conditions": conditions},
        )
