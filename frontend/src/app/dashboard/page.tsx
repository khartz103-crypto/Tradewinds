"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";
import { apiGet } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";

interface PortfolioSummary {
  account_value: number | string;
  buying_power: number | string;
  today_pnl: number | string;
  total_return_pct: number | string;
  open_positions: number;
  daily_pnl: number | string;
  circuit_breaker_active: boolean;
  initial_balance: number | string;
  current_balance: number | string;
}

interface EquityPoint {
  time: UTCTimestamp;
  value: number;
}

const REFRESH_MS = 30_000;

export default function DashboardPage() {
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [equityPoints, setEquityPoints] = useState<EquityPoint[]>([]);
  const [chartReady, setChartReady] = useState(false);

  const chartContainerRef = useRef<HTMLDivElement>(null);
  // Chart/series instances live in refs so the portfolio update effect can
  // push points without recreating the chart.
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const hasFedDataRef = useRef(false);

  const fetchPortfolio = useCallback(async () => {
    try {
      const data = await apiGet<PortfolioSummary>("/api/paper/portfolio");
      setPortfolio(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load portfolio");
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on mount + poll every 30s.
  useEffect(() => {
    fetchPortfolio();
    const interval = setInterval(fetchPortfolio, REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchPortfolio]);

  // Initialize the equity curve chart once the container is mounted.
  // lightweight-charts is loaded lazily so it never runs during SSR.
  useEffect(() => {
    let cancelled = false;
    let chart: IChartApi | null = null;

    (async () => {
      if (!chartContainerRef.current || cancelled) return;
      const { createChart, ColorType, LineSeries } = await import(
        "lightweight-charts"
      );
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
        pointMarkersVisible: true,
        lastValueVisible: true,
      });
      if (!cancelled) setChartReady(true);
    })();

    return () => {
      cancelled = true;
      chartRef.current = null;
      seriesRef.current = null;
      hasFedDataRef.current = false;
      chart?.remove();
    };
  }, []);

  // Record an equity point whenever the account value moves. The first
  // successful poll seeds the line; later polls only append when the value
  // actually changed, so the curve grows without flat duplicates.
  useEffect(() => {
    if (!portfolio) return;
    const value = Number(portfolio.account_value);
    if (!Number.isFinite(value)) return;
    const now = Math.floor(Date.now() / 1000) as UTCTimestamp;
    setEquityPoints((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.value === value) return prev;
      return [...prev, { time: now, value }];
    });
  }, [portfolio]);

  // Feed new points into the chart once it exists. The first sync seeds the
  // whole series; every later sync pushes just the newest point.
  useEffect(() => {
    if (!chartReady) return;
    const series = seriesRef.current;
    if (!series || equityPoints.length === 0) return;
    if (!hasFedDataRef.current) {
      series.setData(equityPoints);
      hasFedDataRef.current = true;
    } else {
      series.update(equityPoints[equityPoints.length - 1]);
    }
    chartRef.current?.timeScale().fitContent();
  }, [equityPoints, chartReady]);

  const isLoading = loading && !portfolio;

  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-100">Portfolio</h2>

      {portfolio?.circuit_breaker_active && (
        <div className="mt-4 rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm font-medium text-red-300">
          ⚠️ Circuit breaker activated — trading halted due to daily loss limit.
        </div>
      )}

      {error && portfolio && (
        <div className="mt-4 rounded-lg border border-amber-800 bg-amber-950/40 px-4 py-2 text-sm text-amber-300">
          Refresh failed: {error}
        </div>
      )}

      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-gray-700 border-t-blue-500" />
        </div>
      ) : error && !portfolio ? (
        <div className="mt-8 rounded-xl border border-gray-800 bg-gray-900 p-8 text-center">
          <p className="text-sm text-gray-400">
            Failed to load portfolio: {error}
          </p>
          <button
            onClick={fetchPortfolio}
            className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          {/* Stats row */}
          <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
            <StatCard label="Account Value">
              {formatCurrency(portfolio?.account_value ?? 0)}
            </StatCard>
            <StatCard label="Buying Power">
              {formatCurrency(portfolio?.buying_power ?? 0)}
            </StatCard>
            <StatCard label="Today's P&L">
              <PnlValue value={portfolio?.today_pnl ?? 0} format={formatCurrency} />
            </StatCard>
            <StatCard label="Total Return">
              <ReturnValue value={portfolio?.total_return_pct ?? 0} />
            </StatCard>
            <StatCard label="Open Positions">
              <span className="inline-flex items-center gap-2">
                <span className="rounded-full bg-gray-700 px-2.5 py-0.5 text-sm font-bold text-gray-100">
                  {portfolio?.open_positions ?? 0}
                </span>
              </span>
            </StatCard>
          </div>

          {/* Equity curve */}
          <div className="mt-6 rounded-xl border border-gray-800 bg-gray-800 p-5">
            <div className="flex items-baseline justify-between">
              <h3 className="text-sm font-semibold text-gray-200">Equity Curve</h3>
              <span className="text-xs text-gray-500">
                {formatCurrency(portfolio?.account_value ?? 0)}
              </span>
            </div>
            <div ref={chartContainerRef} className="mt-4 h-64 w-full" />
            <p className="mt-3 text-xs text-gray-500">
              {equityPoints.length > 0
                ? `${equityPoints.length} sample${equityPoints.length === 1 ? "" : "s"} · new points are added on each poll when the account value changes.`
                : "Waiting for portfolio data to start the equity curve…"}
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-700 bg-gray-800 p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-gray-400">
        {label}
      </p>
      <p className="mt-2 text-xl font-semibold text-gray-100">{children}</p>
    </div>
  );
}

function PnlValue({
  value,
  format,
}: {
  value: number | string;
  format: (v: number | string) => string;
}) {
  const num = Number(value);
  const cls = num > 0 ? "text-emerald-400" : num < 0 ? "text-red-400" : "text-gray-100";
  return <span className={cls}>{format(value)}</span>;
}

function ReturnValue({ value }: { value: number | string }) {
  const num = Number(value);
  const cls = num > 0 ? "text-emerald-400" : num < 0 ? "text-red-400" : "text-gray-100";
  const arrow = num > 0 ? " ▲" : num < 0 ? " ▼" : "";
  return (
    <span className={cls}>
      {formatPercent(value)}
      {arrow}
    </span>
  );
}
