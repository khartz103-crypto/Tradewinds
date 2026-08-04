"""Trend-following strategy using moving-average crossovers + ADX confirmation."""

from __future__ import annotations

from app.schemas.market_data import Bar
from app.strategies import BaseStrategy, StrategySignal, register_strategy
from app.strategies.indicators import adx, atr, ema, macd, rsi, sma, volume_sma


@register_strategy
class TrendFollowingStrategy(BaseStrategy):
    """A momentum-based strategy that enters long when short-term MAs are
    above long-term MAs, ADX confirms a trending market, MACD and RSI provide
    momentum alignment, and volume confirms participation.

    Config keys (all optional, defaults shown):
        min_signals: int    = 4     Minimum conditions met to fire a signal
        short_window: int   = 20    EMA / SMA short period
        long_window: int    = 50    EMA / SMA long period
        adx_threshold: int  = 25    Minimum ADX for trending market
        adx_period: int     = 14    ADX calculation period
        volume_factor: float = 1.5  Min volume / avg-volume ratio
        macd_fast: int      = 12    MACD fast EMA period
        macd_slow: int      = 26    MACD slow EMA period
        macd_signal: int    = 9     MACD signal line period
        rsi_period: int     = 14    RSI calculation period
        rsi_low: float      = 40.0  RSI lower bound (not oversold)
        rsi_high: float     = 70.0  RSI upper bound (not overbought)
        atr_period: int     = 14    ATR calculation period
        atr_stop_mult: float = 2.0  Stop-loss = close - (mult * ATR)
        atr_target_mult: float = 3.0  Take-profit = close + (mult * ATR)
    """

    name = "trend_following"
    display_name = "Trend Following"

    # ── defaults ──────────────────────────────────────────────────────

    DEFAULTS: dict = {
        "min_signals": 4,
        "short_window": 20,
        "long_window": 50,
        "adx_threshold": 25,
        "adx_period": 14,
        "volume_factor": 1.5,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "rsi_period": 14,
        "rsi_low": 40.0,
        "rsi_high": 70.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "atr_target_mult": 3.0,
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
        min_bars = max(
            self._cfg("long_window"),
            self._cfg("adx_period"),
            self._cfg("rsi_period"),
            self._cfg("macd_slow"),
            self._cfg("atr_period"),
        ) + 1

        if len(bars) < min_bars:
            return None

        closes = [float(b.close) for b in bars]
        highs = [float(b.high) for b in bars]
        lows = [float(b.low) for b in bars]
        volumes = [int(b.volume) for b in bars]

        short = self._cfg("short_window")
        long = self._cfg("long_window")
        adx_period = self._cfg("adx_period")
        adx_threshold = self._cfg("adx_threshold")
        volume_factor = self._cfg("volume_factor")

        # --- compute all indicators ----------------------------------
        ema_short = ema(closes, short)
        ema_long = ema(closes, long)
        sma_short = sma(closes, short)
        sma_long = sma(closes, long)
        adx_line, plus_di, minus_di = adx(highs, lows, closes, adx_period)
        macd_line, macd_signal_line, macd_histogram = macd(
            closes,
            fast=self._cfg("macd_fast"),
            slow=self._cfg("macd_slow"),
            signal=self._cfg("macd_signal"),
        )
        rsi_line = rsi(closes, self._cfg("rsi_period"))
        atr_line = atr(highs, lows, closes, self._cfg("atr_period"))
        vol_sma_line = volume_sma(volumes, short)

        # --- extract latest values -----------------------------------
        latest_ema_s = self._last(ema_short)
        latest_ema_l = self._last(ema_long)
        latest_sma_s = self._last(sma_short)
        latest_sma_l = self._last(sma_long)
        latest_adx = self._last(adx_line)
        latest_macd = self._last(macd_line)
        latest_macd_sig = self._last(macd_signal_line)
        latest_rsi = self._last(rsi_line)
        latest_atr = self._last(atr_line)
        latest_vol_sma = self._last(vol_sma_line)
        latest_close = closes[-1]
        latest_volume = volumes[-1]

        # --- collect indicator values for debugging ------------------
        indicators = {
            "ema_short": latest_ema_s,
            "ema_long": latest_ema_l,
            "sma_short": latest_sma_s,
            "sma_long": latest_sma_l,
            "adx": latest_adx,
            "plus_di": self._last(plus_di),
            "minus_di": self._last(minus_di),
            "macd_line": latest_macd,
            "macd_signal": latest_macd_sig,
            "macd_histogram": self._last(macd_histogram),
            "rsi": latest_rsi,
            "atr": latest_atr,
            "volume_sma": latest_vol_sma,
            "latest_close": latest_close,
            "latest_volume": latest_volume,
        }

        none_indicators = any(
            v is None
            for v in [
                latest_ema_s,
                latest_ema_l,
                latest_sma_s,
                latest_sma_l,
                latest_adx,
                latest_macd,
                latest_macd_sig,
                latest_rsi,
                latest_atr,
                latest_vol_sma,
            ]
        )
        if none_indicators:
            return None

        # --- evaluate conditions -------------------------------------

        # 1. EMA alignment: EMA-short > EMA-long
        cond_ema = bool(latest_ema_s > latest_ema_l)  # type: ignore[operator]

        # 2. SMA alignment: SMA-short > SMA-long
        cond_sma = bool(latest_sma_s > latest_sma_l)  # type: ignore[operator]

        # 3. ADX > threshold (trending market)
        cond_adx = bool(latest_adx > adx_threshold)  # type: ignore[operator]

        # 4. MACD line > MACD signal (momentum)
        cond_macd = bool(latest_macd > latest_macd_sig)  # type: ignore[operator]

        # 5. RSI between 40-70
        rsi_low = self._cfg("rsi_low")
        rsi_high = self._cfg("rsi_high")
        cond_rsi = bool(rsi_low < latest_rsi < rsi_high)  # type: ignore[operator]

        # 6. Volume > volume_factor * volume SMA
        cond_volume = bool(latest_volume > volume_factor * latest_vol_sma)  # type: ignore[operator]

        conditions = {
            "ema_alignment": cond_ema,
            "sma_alignment": cond_sma,
            "adx_trending": cond_adx,
            "macd_momentum": cond_macd,
            "rsi_zone": cond_rsi,
            "volume_confirmation": cond_volume,
        }

        # --- threshold-based signal decision ---------------------------
        # A signal fires when at least `min_signals` of the 6 conditions
        # align in one direction (4/6 by default) — far more frequent than
        # requiring all 6.
        bullish_count = sum(1 for v in conditions.values() if v)
        bearish_count = sum(1 for v in conditions.values() if not v)
        min_sig = self._cfg("min_signals")
        total_conditions = len(conditions)

        if bullish_count >= min_sig:
            action = "buy"
        elif bearish_count >= min_sig:
            action = "sell"
        else:
            return None

        # --- compute signal levels -----------------------------------
        entry_price = latest_close
        stop_loss: float | None = None
        take_profit: float | None = None

        if action == "buy" and latest_atr is not None:
            stop_loss = entry_price - self._cfg("atr_stop_mult") * latest_atr
            take_profit = entry_price + self._cfg("atr_target_mult") * latest_atr
        elif action == "sell" and latest_atr is not None:
            stop_loss = entry_price + self._cfg("atr_stop_mult") * latest_atr
            take_profit = entry_price - self._cfg("atr_target_mult") * latest_atr

        # --- confidence score (fraction of conditions met) ------------
        # Scale by how many conditions passed in the signal direction.
        passed = bullish_count if action == "buy" else bearish_count
        confidence = (passed / total_conditions) * 100.0

        # --- reasoning ------------------------------------------------
        condition_detail = ", ".join(
            f"{name}=PASS" if (result if action == "buy" else not result)
            else f"{name}=FAIL"
            for name, result in conditions.items()
        )
        reasoning = (
            f"{action.upper()} signal for {symbol} ({self.display_name}). "
            f"({passed}/{total_conditions} conditions met): [{condition_detail}]. "
            f"Entry: {entry_price:.2f}, SL: {stop_loss:.2f}, TP: {take_profit:.2f}. "
            f"Confidence: {confidence:.1f}%"
        )

        return StrategySignal(
            symbol=symbol,
            action=action,
            confidence=round(confidence, 1),
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2) if stop_loss is not None else None,
            take_profit=round(take_profit, 2) if take_profit is not None else None,
            reasoning=reasoning,
            indicators=indicators,
        )
