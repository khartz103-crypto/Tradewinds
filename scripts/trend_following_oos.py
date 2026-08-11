"""Trend Following — 2yr IS / 2yr OOS / 4yr full validation on real Yahoo data.

DB-free: monkeypatches ``backtest._load_strategy`` and ``backtest.get_daily_bars``
so the engine runs purely on Yahoo bars fetched once for the full 4yr span
(engine clips each run to its sub-window). Same pattern as sweep_breakout_oos.py.

Usage: /var/tmp/tw-venv/bin/python /tmp/trend_following_oos.py
"""
import asyncio
import os
import pickle
import sys
from datetime import datetime

sys.path.insert(0, "/home/agent-lead/Tradewinds/backend")

BAR_CACHE = "/tmp/trend_bars.pkl"

from app.providers.yahoo import YahooProvider
from app.schemas.market_data import Bar
from app.services import backtest as bt
from app.strategies import get_strategy

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD", "QQQ", "SPY"]

FETCH_START = "2022-08-06"   # full 4yr fetch window (clipped per run)
FETCH_END = "2026-08-06"
WINDOWS = [
    ("2yr IS",  "2022-08-06", "2024-08-06"),
    ("2yr OOS", "2024-08-06", "2026-08-06"),
    ("4yr full","2022-08-06", "2026-08-06"),
]


def fmt(x, nd=2):
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}"


async def fetch_all(provider) -> dict:
    if os.path.exists(BAR_CACHE):
        with open(BAR_CACHE, "rb") as f:
            cached = pickle.load(f)
        print(f"Loaded {len(cached)} symbols from cache {BAR_CACHE}")
        for sym, bars in cached.items():
            print(f"  {sym}: {len(bars)} bars")
        return cached
    bars_map = {}
    start = datetime.strptime(FETCH_START, "%Y-%m-%d")
    end = datetime.strptime(FETCH_END, "%Y-%m-%d")
    print(f"Fetching full window {FETCH_START} -> {FETCH_END} ...")
    for sym in SYMBOLS:
        try:
            raw = await provider.get_bars(sym, start, end)
        except Exception as exc:
            print(f"  {sym}: FAILED {exc}")
            continue
        bars = [Bar(**r) for r in raw]
        if bars:
            bars_map[sym.upper()] = bars
            print(f"  {sym}: {len(bars)} bars")
        else:
            print(f"  {sym}: EMPTY")
    with open(BAR_CACHE, "wb") as f:
        pickle.dump(bars_map, f)
    print(f"Cached bars -> {BAR_CACHE}")
    return bars_map


async def run_window(label: str, start_s: str, end_s: str, strategy, bars_map) -> dict:
    start_date = datetime.strptime(start_s, "%Y-%m-%d")
    end_date = datetime.strptime(end_s, "%Y-%m-%d")

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
    print(f"trend_following | {label} | {start_s} -> {end_s}")
    print("=" * 78)
    print(
        f"trades={m['trade_count']}  win_rate={fmt(m['win_rate_pct'])}%  "
        f"profit_factor={fmt(m['profit_factor'])}  return={fmt(m['total_return_pct'])}%  "
        f"sharpe={fmt(m['sharpe_ratio'])}  max_dd={fmt(m['max_drawdown_pct'])}%  "
        f"expectancy=${fmt(m['expectancy'], 0)}  avg_r={fmt(m['mean_r_multiple'])}  "
        f"cagr={fmt(m['cagr_pct'])}%  exposure={fmt(m['exposure_pct'])}%"
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
    return m


async def main() -> None:
    provider = YahooProvider()
    bars_map = await fetch_all(provider)
    if not bars_map:
        print("No data — aborting.")
        return
    strategy = get_strategy("trend_following")
    results = {}
    for label, start_s, end_s in WINDOWS:
        results[label] = await run_window(label, start_s, end_s, strategy, bars_map)

    print("=" * 78)
    print("SUMMARY TABLE (full precision)")
    print("=" * 78)
    print(f"{'Window':<10}{'Trades':>8}{'Win%':>8}{'PF':>10}{'Return%':>10}"
          f"{'Sharpe':>12}{'MaxDD%':>10}")
    for label, _, _ in WINDOWS:
        m = results[label]
        print(f"{label:<10}{m['trade_count']:>8}{m['win_rate_pct']:>8.3f}"
              f"{m['profit_factor']:>10.4f}{m['total_return_pct']:>10.3f}"
              f"{m['sharpe_ratio']:>12.6f}{m['max_drawdown_pct']:>10.3f}")


if __name__ == "__main__":
    asyncio.run(main())
