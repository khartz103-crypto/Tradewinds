"""SPY-above-200-SMA regime filter test on Trend Following.

DB-free variant of the OOS validation: monkeypatches ``backtest._load_strategy``
and ``backtest.get_daily_bars`` so the engine runs purely on cached Yahoo bars.
Adds a regime gate on top: before any Trend Following signal is accepted, the
SPY close must be strictly above SPY's 200-day SMA on the signal day; otherwise
the signal is skipped (blocked). Exits (stop/target management) are untouched —
the gate only filters new entries.

SPY's 200-SMA is computed from an extended SPY history (2021-08-06 → 2026-08-06,
1 year pre-window) so the indicator is defined for the entire 4yr window —
mirroring production, where the indicator always has full prior history.
All other symbols use the shared 4yr cache (2022-08-06 → 2026-08-06).

Usage: /var/tmp/tw-venv/bin/python /tmp/regime_filter_spy200.py
"""
import asyncio
import os
import pickle
import sys
from collections import Counter
from datetime import datetime, date

sys.path.insert(0, "/home/agent-lead/Tradewinds/backend")

BAR_CACHE = "/tmp/trend_bars.pkl"        # 10 symbols, 2022-08-06 -> 2026-08-06
SPY_EXT_CACHE = "/tmp/spy_ext_bars.pkl"  # SPY only, 2021-08-06 -> 2026-08-06

from app.providers.yahoo import YahooProvider
from app.schemas.market_data import Bar
from app.services import backtest as bt
from app.strategies import get_strategy
from app.strategies.indicators import sma

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD", "QQQ", "SPY"]

FETCH_START = "2022-08-06"
FETCH_END = "2026-08-06"
SPY_PRE_START = "2021-08-06"   # 1yr pre-window so SPY SMA200 is defined from day 1
SPY_SMA_PERIOD = 200
WINDOWS = [
    ("4yr full", "2022-08-06", "2026-08-06"),
    ("2yr OOS",  "2024-08-06", "2026-08-06"),
]


def fmt(x, nd=2):
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}"


async def _fetch(provider, symbol, start_s, end_s) -> list[Bar]:
    start = datetime.strptime(start_s, "%Y-%m-%d")
    end = datetime.strptime(end_s, "%Y-%m-%d")
    raw = await provider.get_bars(symbol, start, end)
    return [Bar(**r) for r in raw]


async def load_bars(provider) -> dict:
    """Load the shared 4yr cache; fetch it if missing. Always ensure the
    extended SPY history exists (fetch+cache once)."""
    bars_map = {}
    if os.path.exists(BAR_CACHE):
        with open(BAR_CACHE, "rb") as f:
            bars_map = pickle.load(f)
        print(f"Loaded {len(bars_map)} symbols from cache {BAR_CACHE}")
    else:
        print(f"Fetching full window {FETCH_START} -> {FETCH_END} ...")
        for sym in SYMBOLS:
            try:
                bars = await _fetch(provider, sym, FETCH_START, FETCH_END)
            except Exception as exc:
                print(f"  {sym}: FAILED {exc}")
                continue
            bars_map[sym.upper()] = bars if bars else []
            print(f"  {sym}: {len(bars)} bars")
        with open(BAR_CACHE, "wb") as f:
            pickle.dump(bars_map, f)
        print(f"Cached bars -> {BAR_CACHE}")

    if os.path.exists(SPY_EXT_CACHE):
        with open(SPY_EXT_CACHE, "rb") as f:
            spy_ext = pickle.load(f)
        print(f"Loaded SPY extended history from {SPY_EXT_CACHE} ({len(spy_ext)} bars)")
    else:
        print(f"Fetching SPY extended history {SPY_PRE_START} -> {FETCH_END} ...")
        spy_ext = await _fetch(provider, "SPY", SPY_PRE_START, FETCH_END)
        with open(SPY_EXT_CACHE, "wb") as f:
            pickle.dump(spy_ext, f)
        print(f"SPY extended: {len(spy_ext)} bars -> {SPY_EXT_CACHE}")
    bars_map["SPY_EXT"] = spy_ext
    return bars_map


def build_spy_regime(spy_ext_bars: list[Bar]):
    """Return (spy_above, spy_close, spy_sma) dicts keyed by date.

    spy_above[d] is True iff SPY close > SPY SMA200 on day d (both defined).
    Computed from the extended history so SMA200 exists for the whole 4yr window.
    """
    spy_ext_bars = sorted(spy_ext_bars, key=lambda b: b.timestamp.date())
    closes = [float(b.close) for b in spy_ext_bars]
    sma200 = sma(closes, SPY_SMA_PERIOD)
    spy_close: dict[date, float] = {}
    spy_sma: dict[date, float] = {}
    spy_above: dict[date, bool] = {}
    for bar, s in zip(spy_ext_bars, sma200):
        d = bar.timestamp.date()
        spy_close[d] = float(bar.close)
        if s is not None:
            spy_sma[d] = s
            spy_above[d] = float(bar.close) > s
        else:
            spy_above[d] = False  # no SMA yet -> gate closed (conservative)
    return spy_above, spy_close, spy_sma


async def run_window(
    label: str,
    start_s: str,
    end_s: str,
    bars_map: dict,
    spy_above: dict | None,
) -> tuple[dict, Counter]:
    """Run one window. If spy_above is not None, wrap analyze() with the gate."""
    start_date = datetime.strptime(start_s, "%Y-%m-%d")
    end_date = datetime.strptime(end_s, "%Y-%m-%d")
    strategy = get_strategy("trend_following")
    counter: Counter = Counter()

    if spy_above is not None:
        orig_analyze = strategy.analyze

        async def gated_analyze(symbol, bars):
            counter["analyze_calls"] += 1
            day = bars[-1].timestamp.date()
            above = spy_above.get(day, False)
            if not above:
                counter["gated_blocked"] += 1
                return None
            signal = await orig_analyze(symbol, bars)
            if signal is not None:
                counter["signals_passed"] += 1
            return signal

        strategy.analyze = gated_analyze  # type: ignore[method-assign]

    async def fake_load_strategy(db, name, overrides):
        return strategy

    async def fake_get_daily_bars(db, symbol, s, e):
        return list(bars_map.get(symbol.upper(), []))

    bt._load_strategy = fake_load_strategy
    bt.get_daily_bars = fake_get_daily_bars

    result = await bt.run_backtest(
        db=object(),
        strategy_name="trend_following",
        symbols=SYMBOLS,
        start_date=start_date,
        end_date=end_date,
        risk_per_trade=0.005,
        max_open_positions=10,
        max_portfolio_exposure_pct=80.0,
        slippage=0.0,
        initial_balance=100_000,
    )
    m = result.metrics
    print("=" * 78)
    print(f"trend_following | {'SPY>200SMA GATED' if spy_above is not None else 'BASELINE      '} | {label} | {start_s} -> {end_s}")
    print("=" * 78)
    print(
        f"trades={m['trade_count']}  win_rate={fmt(m['win_rate_pct'])}%  "
        f"profit_factor={fmt(m['profit_factor'])}  return={fmt(m['total_return_pct'])}%  "
        f"sharpe={fmt(m['sharpe_ratio'])}  max_dd={fmt(m['max_drawdown_pct'])}%  "
        f"expectancy=${fmt(m['expectancy'], 0)}  avg_r={fmt(m['mean_r_multiple'])}  "
        f"cagr={fmt(m['cagr_pct'])}%  exposure={fmt(m['exposure_pct'])}%"
    )
    if counter:
        print(
            f"gate: analyze_calls={counter['analyze_calls']}  "
            f"blocked_by_gate={counter['gated_blocked']}  "
            f"passed={counter['signals_passed']}"
        )
    print("-" * 78)
    per_symbol = m.get("per_symbol", {})
    if per_symbol:
        print(f"{'SYM':<8}{'TRADES':>8}{'WIN%':>8}{'TOTAL PNL':>12}{'AVG R':>8}")
        for sym in sorted(per_symbol, key=lambda s: per_symbol[s]["total_pnl"], reverse=True):
            e = per_symbol[sym]
            print(
                f"{sym:<8}{e['trade_count']:>8}{fmt(e['win_rate_pct']):>8}"
                f"{fmt(e['total_pnl'], 0):>12}{fmt(e['avg_r_multiple']):>8}"
            )
    print()
    return m, counter


async def main() -> None:
    provider = YahooProvider()
    bars_map = await load_bars(provider)
    spy_ext = bars_map.pop("SPY_EXT")
    spy_above, spy_close, spy_sma = build_spy_regime(spy_ext)
    if not spy_above:
        print("No SPY data — aborting.")
        return

    # Context stats: % of trading days SPY above its 200-SMA in each window.
    for label, start_s, end_s in WINDOWS:
        s = datetime.strptime(start_s, "%Y-%m-%d").date()
        e = datetime.strptime(end_s, "%Y-%m-%d").date()
        days = [d for d in spy_above if s <= d <= e]
        above = sum(1 for d in days if spy_above[d])
        defined = sum(1 for d in days if d in spy_sma)
        print(
            f"[context] {label}: {len(days)} SPY trading days, "
            f"SPY>200SMA on {above} ({above / len(days) * 100:.1f}%), "
            f"SMA defined on {defined} days"
        )
    print()

    results = {}
    for label, start_s, end_s in WINDOWS:
        results[("baseline", label)], _ = await run_window(
            label, start_s, end_s, bars_map, None
        )
        results[("gated", label)], _ = await run_window(
            label, start_s, end_s, bars_map, spy_above
        )

    print("=" * 78)
    print("SUMMARY TABLE (full precision)")
    print("=" * 78)
    print(f"{'Variant':<12}{'Window':<10}{'Trades':>8}{'Win%':>9}{'PF':>11}{'Return%':>11}"
          f"{'Sharpe':>13}{'MaxDD%':>10}")
    for variant in ("baseline", "gated"):
        for label, _, _ in WINDOWS:
            m = results[(variant, label)]
            print(f"{variant:<12}{label:<10}{m['trade_count']:>8}{m['win_rate_pct']:>9.3f}"
                  f"{m['profit_factor']:>11.4f}{m['total_return_pct']:>11.3f}"
                  f"{m['sharpe_ratio']:>13.6f}{m['max_drawdown_pct']:>10.3f}")
    print()
    print("DELTAS (gated - baseline)")
    for label, _, _ in WINDOWS:
        b = results[("baseline", label)]
        g = results[("gated", label)]
        print(
            f"  {label:<10} trades {b['trade_count']} -> {g['trade_count']} "
            f"({g['trade_count'] - b['trade_count']:+d}); "
            f"Sharpe {b['sharpe_ratio']:.4f} -> {g['sharpe_ratio']:.4f} "
            f"({g['sharpe_ratio'] - b['sharpe_ratio']:+.4f}); "
            f"PF {b['profit_factor']:.4f} -> {g['profit_factor']:.4f}; "
            f"Return {b['total_return_pct']:.2f}% -> {g['total_return_pct']:.2f}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
