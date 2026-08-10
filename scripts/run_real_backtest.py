"""Run profitability backtests for ALL Tradewinds strategies on real Yahoo data.

DB-free: monkeypatches ``backtest._load_strategy`` and ``backtest.get_daily_bars``
so the engine runs purely on Yahoo bars fetched via the app's own provider.

Usage:
    python /tmp/run_real_backtest.py                 # all 4 strategies x 2/4yr
    python /tmp/run_real_backtest.py breakout 2      # single combo

Run from /tmp (never from inside the repo) so the venv import path is clean.
"""
import sys
import asyncio
from datetime import datetime

sys.path.insert(0, "/home/agent-lead/Tradewinds/backend")

from app.providers.yahoo import YahooProvider
from app.schemas.market_data import Bar
from app.services import backtest as bt
from app.strategies import get_strategy

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "AMD", "QQQ", "SPY"]
STRATEGIES = ["trend_following", "mean_reversion", "momentum_pullback", "breakout"]
WINDOWS = [2, 4]
END_DATE = "2026-08-06"


def fmt(x, nd=2):
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}"


async def run_one(strategy_name: str, years: int, bars_map: dict) -> None:
    """Run one backtest combo and print metrics + per-symbol breakdown."""
    start_date = f"{2026 - years}-08-06"
    strategy = get_strategy(strategy_name)

    async def fake_load_strategy(db, name, overrides):
        return strategy

    async def fake_get_daily_bars(db, symbol, start, end):
        return bars_map[symbol.upper()]

    bt._load_strategy = fake_load_strategy
    bt.get_daily_bars = fake_get_daily_bars

    result = await bt.run_backtest(
        db=object(),
        strategy_name=strategy_name,
        symbols=SYMBOLS,
        start_date=datetime.strptime(start_date, "%Y-%m-%d"),
        end_date=datetime.strptime(END_DATE, "%Y-%m-%d"),
        risk_per_trade=0.005,
        max_open_positions=10,
    )

    m = result.metrics
    print("=" * 78)
    print(f"{strategy_name} | {years}yr window | {start_date} -> {END_DATE}")
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


async def main() -> None:
    # Optional CLI: [strategy] [years] → single combo
    strategy_filter = sys.argv[1] if len(sys.argv) > 1 else None
    years_filter = int(sys.argv[2]) if len(sys.argv) > 2 else None

    combos = [
        (s, y) for s in STRATEGIES for y in WINDOWS
        if (strategy_filter is None or s == strategy_filter)
        and (years_filter is None or y == years_filter)
    ]

    # Fetch the full 4-year span once per symbol; backtest clips to its window.
    provider = YahooProvider()
    fetch_start = f"{2026 - max(WINDOWS)}-08-06"
    bars_map: dict[str, list[Bar]] = {}
    for sym in SYMBOLS:
        rows = await provider.get_bars(
            sym,
            datetime.strptime(fetch_start, "%Y-%m-%d"),
            datetime.strptime(END_DATE, "%Y-%m-%d"),
        )
        bars_map[sym.upper()] = [Bar(**row) for row in rows]
        print(f"Fetched {len(bars_map[sym.upper()])} bars for {sym}", flush=True)
    print("Fetch complete. Running backtests...\n", flush=True)

    for strategy_name, years in combos:
        await run_one(strategy_name, years, bars_map)


if __name__ == "__main__":
    asyncio.run(main())
