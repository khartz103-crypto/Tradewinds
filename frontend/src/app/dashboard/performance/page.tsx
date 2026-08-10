"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiGet } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";

interface EquityCurvePoint {
  date: string;
  equity: number;
}

interface PerSymbol {
  symbol: string;
  trade_count: number;
  total_pnl: number;
  win_rate_pct: number;
  avg_r_multiple: number;
}

interface RecentTrade {
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  entry_date: string;
  exit_date: string;
  pnl: number;
  holding_days: number;
}

interface DashboardPerformance {
  current_equity: number | string;
  starting_balance: number | string;
  total_return_pct: number | string;
  total_pnl: number | string;
  open_positions: number;
  total_trades_closed: number;
  win_rate_pct: number | string;
  profit_factor: number | string | null;
  sharpe_ratio: number | string;
  max_drawdown_pct: number | string;
  avg_holding_days: number | string;
  per_symbol: PerSymbol[];
  equity_curve: EquityCurvePoint[];
  recent_trades: RecentTrade[];
}

interface ChartPoint {
  time: UTCTimestamp;
  value: number;
}

const REFRESH_MS = 30_000;

function sharpeClass(value: number) {
  if (value >= 1) return "text-emerald-400";
  if (value >= 0) return "text-amber-400";
  return "text-red-400";
}

function pnlClass(value: number) {
  if (value > 0) return "text-emerald-400";
  if (value < 0) return "text-red-400";
  return "text-gray-300";
}

function sideClass(side: string) {
  return side.toLowerCase() === "long"
    ? "bg-emerald-950 text-emerald-400"
    : "bg-red-950 text-red-400";
}

function formatProfitFactor(value: number | string | null) {
  if (value === null || value === undefined) return "∞";
  return Number(value).toFixed(2);
}

export default function PerformancePage() {
  const [data, setData] = useState<DashboardPerformance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  const fetchPerformance = useCallback(async () => {
    try {
      const payload = await apiGet<DashboardPerformance>("/api/dashboard/performance");
      setData(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load performance");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPerformance();
    const interval = setInterval(fetchPerformance, REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchPerformance]);

  // Initialize the equity-curve chart once the container is mounted.
  // lightweight-charts is loaded lazily so it never runs during SSR.
  useEffect(() => {
    let cancelled = false;
    let chart: IChartApi | null = null;
    (async () => {
      if (!chartContainerRef.current || cancelled) return;
      const { createChart, ColorType, LineSeries } = await import("lightweight-charts");
      if (!chartContainerRef.current || cancelled) return;
      chart = createChart(chartContainerRef.current, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: "#9ca3af",
          fontSize: 12,
        },
        grid: {
          vertLines: { color: "rgba(75, 85, 99, 0.2)" },
          horzLines: { color: "rgba(75, 85, 99, 0.2)" },
        },
        rightPriceScale: { borderColor: "rgba(75, 85, 99, 0.4)" },
        timeScale: { borderColor: "rgba(75, 85, 99, 0.4)" },
        crosshair: {
          vertLine: { color: "rgba(148, 163, 184, 0.4)" },
          horzLine: { color: "rgba(148, 163, 184, 0.4)" },
        },
      });
      chartRef.current = chart;
      seriesRef.current = chart.addSeries(LineSeries, {
        color: "#3b82f6",
        lineWidth: 2,
        lastValueVisible: true,
        priceLineVisible: false,
      });
    })();
    return () => {
      cancelled = true;
      chartRef.current = null;
      seriesRef.current = null;
      chart?.remove();
    };
  }, []);

  // Push the fetched equity curve into the chart whenever either side changes.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !data || data.equity_curve.length === 0) return;
    const points: ChartPoint[] = data.equity_curve.map((p) => ({
      time: Math.floor(Date.parse(p.date) / 1000) as UTCTimestamp,
      value: Number(p.equity),
    }));
    series.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-gray-100">
          Performance
        </h1>
        <p className="mt-1 text-sm text-gray-400">
          Live paper-trading metrics · auto-refreshes every 30s
        </p>
      </div>

      {error && !data && (
        <div className="rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center py-24">
          <span className="h-10 w-10 animate-spin rounded-full border-4 border-gray-700 border-t-blue-500" />
        </div>
      ) : (
        data && (
          <>
            {/* Sharpe ratio — the key metric */}
            <section className="rounded-xl border border-gray-700 bg-gray-800 p-6 shadow-lg">
              <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
                Sharpe Ratio
              </p>
              <p className={`mt-2 text-5xl font-extrabold tracking-tight ${sharpeClass(Number(data.sharpe_ratio))}`}>
                {Number(data.sharpe_ratio).toFixed(2)}
              </p>
              <p className="mt-2 text-xs text-gray-500">
                Annualised risk-adjusted return (daily bars, 0% risk-free)
              </p>
            </section>

            {/* Summary cards */}
            <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard label="Total Return">
                <span className={pnlClass(Number(data.total_return_pct))}>
                  {formatPercent(data.total_return_pct)}
                </span>
              </StatCard>
              <StatCard label="Current Equity">
                {formatCurrency(data.current_equity)}
              </StatCard>
              <StatCard label="Open Positions">
                <span className="text-gray-100">{data.open_positions}</span>
              </StatCard>
              <StatCard label="Win Rate">
                <span className="text-gray-100">
                  {Number(data.win_rate_pct).toFixed(1)}%
                </span>
              </StatCard>
            </section>

            {/* Secondary metrics */}
            <section className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
              <MiniCard label="Total P&L" value={<span className={pnlClass(Number(data.total_pnl))}>{formatCurrency(data.total_pnl)}</span>} />
              <MiniCard label="Profit Factor" value={<span className="text-gray-100">{formatProfitFactor(data.profit_factor)}</span>} />
              <MiniCard label="Max Drawdown" value={<span className="text-red-400">{Number(data.max_drawdown_pct).toFixed(2)}%</span>} />
              <MiniCard label="Avg Holding" value={<span className="text-gray-100">{Number(data.avg_holding_days).toFixed(1)}d</span>} />
              <MiniCard label="Trades Closed" value={<span className="text-gray-100">{data.total_trades_closed}</span>} />
              <MiniCard label="Starting Balance" value={<span className="text-gray-100">{formatCurrency(data.starting_balance)}</span>} />
            </section>

            {/* Equity curve */}
            <section className="rounded-xl border border-gray-700 bg-gray-800 p-5 shadow-lg">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-gray-200">
                  Equity Curve
                </h2>
                <span className="text-xs text-gray-500">
                  {data.equity_curve.length} point{data.equity_curve.length === 1 ? "" : "s"}
                </span>
              </div>
              <div ref={chartContainerRef} className="mt-4 h-72 w-full" />
            </section>

            {/* Per-symbol breakdown + recent trades */}
            <section className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div className="rounded-xl border border-gray-700 bg-gray-800 p-5 shadow-lg">
                <h2 className="text-sm font-semibold text-gray-200">
                  Per-Symbol Breakdown
                </h2>
                {data.per_symbol.length === 0 ? (
                  <p className="mt-6 text-center text-sm text-gray-500">
                    No closed trades yet.
                  </p>
                ) : (
                  <table className="mt-4 w-full text-sm">
                    <thead>
                      <tr className="border-b border-gray-700 text-left text-xs uppercase tracking-wide text-gray-400">
                        <th className="pb-2 pr-4 font-medium">Symbol</th>
                        <th className="pb-2 pr-4 text-right font-medium">Trades</th>
                        <th className="pb-2 pr-4 text-right font-medium">Win %</th>
                        <th className="pb-2 pr-4 text-right font-medium">Avg R</th>
                        <th className="pb-2 text-right font-medium">Total P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.per_symbol.map((row) => (
                        <tr key={row.symbol} className="border-b border-gray-800 last:border-0">
                          <td className="py-2.5 pr-4 font-medium text-gray-100">{row.symbol}</td>
                          <td className="py-2.5 pr-4 text-right text-gray-300">{row.trade_count}</td>
                          <td className="py-2.5 pr-4 text-right text-gray-300">
                            {row.win_rate_pct.toFixed(0)}%
                          </td>
                          <td className="py-2.5 pr-4 text-right text-gray-300">
                            {row.avg_r_multiple.toFixed(2)}
                          </td>
                          <td className={`py-2.5 text-right font-semibold ${pnlClass(row.total_pnl)}`}>
                            {formatCurrency(row.total_pnl)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              <div className="rounded-xl border border-gray-700 bg-gray-800 p-5 shadow-lg">
                <h2 className="text-sm font-semibold text-gray-200">
                  Recent Closed Trades
                </h2>
                {data.recent_trades.length === 0 ? (
                  <p className="mt-6 text-center text-sm text-gray-500">
                    No closed trades yet.
                  </p>
                ) : (
                  <div className="mt-4 max-h-96 overflow-auto">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-gray-800">
                        <tr className="border-b border-gray-700 text-left text-xs uppercase tracking-wide text-gray-400">
                          <th className="pb-2 pr-4 font-medium">Symbol</th>
                          <th className="pb-2 pr-4 font-medium">Side</th>
                          <th className="pb-2 pr-4 text-right font-medium">Entry</th>
                          <th className="pb-2 pr-4 text-right font-medium">Exit</th>
                          <th className="pb-2 pr-4 text-right font-medium">Held</th>
                          <th className="pb-2 text-right font-medium">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.recent_trades.map((t, i) => (
                          <tr key={`${t.symbol}-${t.exit_date}-${i}`} className="border-b border-gray-800 last:border-0">
                            <td className="py-2.5 pr-4 font-medium text-gray-100">{t.symbol}</td>
                            <td className="py-2.5 pr-4">
                              <span className={`rounded px-2 py-0.5 text-xs font-semibold capitalize ${sideClass(t.side)}`}>
                                {t.side}
                              </span>
                            </td>
                            <td className="py-2.5 pr-4 text-right text-gray-300">
                              {formatCurrency(t.entry_price)}
                            </td>
                            <td className="py-2.5 pr-4 text-right text-gray-300">
                              {t.exit_price !== null ? formatCurrency(t.exit_price) : "—"}
                            </td>
                            <td className="py-2.5 pr-4 text-right text-gray-300">
                              {t.holding_days.toFixed(1)}d
                            </td>
                            <td className={`py-2.5 text-right font-semibold ${pnlClass(t.pnl)}`}>
                              {formatCurrency(t.pnl)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </section>
          </>
        )
      )}
    </div>
  );
}

function StatCard({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-700 bg-gray-800 p-4 shadow-lg">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-2 text-xl font-semibold text-gray-100">{children}</p>
    </div>
  );
}

function MiniCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-700 bg-gray-800 p-4 shadow-lg">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">{label}</p>
      <p className="mt-2 text-lg font-semibold">{value}</p>
    </div>
  );
}
