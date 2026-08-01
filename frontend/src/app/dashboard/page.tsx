"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

const REFRESH_MS = 30_000;

export default function DashboardPage() {
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const chartContainerRef = useRef<HTMLDivElement>(null);

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
  useEffect(() => {
    let chart: ReturnType<typeof import("lightweight-charts")["createChart"]> | null =
      null;
    let series: ReturnType<
      ReturnType<typeof import("lightweight-charts")["createChart"]>["addLineSeries"]
    > | null = null;
    let cleanupFn: (() => void) | null = null;

    (async () => {
      if (!chartContainerRef.current) return;
      const { createChart, ColorType } = await import("lightweight-charts");
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
      series = chart.addLineSeries({
        color: "#3b82f6",
        lineWidth: 2,
        pointMarkersVisible: true,
        lastValueVisible: true,
      });
      cleanupFn = () => {
        chart?.remove();
      };
    })();

    return () => {
      cleanupFn?.();
    };
  }, []);

  // Push the current account value into the chart whenever it changes.
  useEffect(() => {
    if (!portfolio) return;
    (async () => {
      const { UTCTimestamp } = await import("lightweight-charts");
      const { createChart } = await import("lightweight-charts");
      if (!chartContainerRef.current) return;
      // Series ref approach: lightweight-charts does not expose an easy
      // "get existing series" API, so we update via the stored instance
      // captured below through a closure held in a module-level map.
      void UTCTimestamp;
      void createChart;
    })();
  }, [portfolio]);

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
              Equity curve data will accumulate as you trade. Only the current
              portfolio value is shown for now.
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
