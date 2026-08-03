"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { formatCurrency, formatPercent } from "@/lib/format";

interface Signal {
  symbol: string;
  action: "buy" | "sell" | string;
  confidence: number | string;
  entry_price: number | string;
  stop_loss: number | string;
  take_profit: number | string;
  reasoning: string;
  indicators: Record<string, number | string | null>;
  timestamp?: string;
  error?: string;
}

interface Strategy {
  name?: string;
  id?: string;
  key?: string;
  display_name?: string;
  enabled?: boolean;
}

const DEFAULT_SYMBOLS = "AAPL\nMSFT\nGOOGL\nAMZN\nTSLA";

function strategyKey(strategy: Strategy) {
  return strategy.key || strategy.name || strategy.id || "";
}

function confidenceClass(value: number) {
  if (value >= 70) return "border-emerald-800 bg-emerald-950/50 text-emerald-300";
  if (value >= 40) return "border-amber-800 bg-amber-950/50 text-amber-300";
  return "border-red-800 bg-red-950/50 text-red-300";
}

function ratio(signal: Signal) {
  const entry = Number(signal.entry_price);
  const stop = Number(signal.stop_loss);
  const target = Number(signal.take_profit);
  const risk = signal.action.toLowerCase() === "sell" ? stop - entry : entry - stop;
  const reward = signal.action.toLowerCase() === "sell" ? entry - target : target - entry;
  if (!Number.isFinite(risk) || !Number.isFinite(reward) || risk <= 0) return "—";
  return `1:${(reward / risk).toFixed(1)}`;
}

export default function ScannerPage() {
  const [symbols, setSymbols] = useState(DEFAULT_SYMBOLS);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [strategy, setStrategy] = useState("trend_following");
  const [signals, setSignals] = useState<Signal[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [strategyLoading, setStrategyLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    apiGet<Strategy[] | { strategies?: Strategy[] }>("/api/strategies")
      .then((result) => {
        const list = Array.isArray(result) ? result : result.strategies || [];
        setStrategies(list.filter((item) => item.enabled !== false));
        const preferred = list.find((item) => strategyKey(item) === "trend_following");
        if (!preferred && list[0]) setStrategy(strategyKey(list[0]));
      })
      .catch(() => {
        // Scanning remains usable with the backend's default strategy if the
        // optional strategy listing endpoint is unavailable.
      })
      .finally(() => setStrategyLoading(false));
  }, []);

  async function scan(event: FormEvent) {
    event.preventDefault();
    const parsedSymbols = symbols
      .split(/[\n,]+/)
      .map((symbol) => symbol.trim().toUpperCase())
      .filter(Boolean);
    if (!parsedSymbols.length) {
      setError("Enter at least one stock symbol to scan.");
      setSignals(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await apiPost<Signal[]>(`/api/strategies/${strategy}/scan`, {
        symbols: parsedSymbols,
      });
      setSignals(result);
    } catch (err) {
      setSignals(null);
      setError(err instanceof Error ? err.message : "Unable to scan the market.");
    } finally {
      setLoading(false);
    }
  }

  const selectedStrategyLabel = useMemo(() => {
    const selected = strategies.find((item) => strategyKey(item) === strategy);
    return selected?.display_name || selected?.name || strategy.replaceAll("_", " ");
  }, [strategies, strategy]);

  return (
    <div className="mx-auto max-w-6xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Market Scanner</h1>
        <p className="mt-1 text-sm text-gray-400">Find actionable signals with explainable strategy reasoning.</p>
      </div>

      <form onSubmit={scan} className="mt-6 rounded-xl border border-gray-700 bg-gray-800 p-5 shadow-lg">
        <div className="grid gap-5 md:grid-cols-[1fr_220px]">
          <label className="block text-sm font-medium text-gray-300">
            Symbols
            <textarea
              value={symbols}
              onChange={(event) => setSymbols(event.target.value)}
              rows={5}
              placeholder="AAPL&#10;MSFT&#10;GOOGL"
              className="mt-2 w-full resize-y rounded-lg border border-gray-600 bg-gray-900 px-3 py-2.5 font-mono text-sm text-gray-100 outline-none transition focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
            <span className="mt-1 block text-xs text-gray-500">Enter one symbol per line (commas are also accepted).</span>
          </label>
          <label className="block text-sm font-medium text-gray-300">
            Strategy
            <select
              value={strategy}
              onChange={(event) => setStrategy(event.target.value)}
              disabled={strategyLoading}
              className="mt-2 w-full rounded-lg border border-gray-600 bg-gray-900 px-3 py-2.5 text-sm capitalize text-gray-100 outline-none focus:border-blue-500"
            >
              {strategies.length === 0 && <option value="trend_following">Trend Following</option>}
              {strategies.map((item) => (
                <option key={strategyKey(item)} value={strategyKey(item)}>
                  {item.display_name || item.name || strategyKey(item).replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={loading}
              className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loading && <span className="h-4 w-4 animate-spin rounded-full border-2 border-blue-200 border-t-transparent" />}
              {loading ? "Scanning…" : "Scan Market"}
            </button>
          </label>
        </div>
      </form>

      {error && <div className="mt-5 rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-300">{error}</div>}

      {loading && (
        <div className="flex min-h-48 items-center justify-center text-gray-400">
          <span className="h-8 w-8 animate-spin rounded-full border-4 border-gray-700 border-t-blue-500" aria-label="Loading" />
        </div>
      )}

      {!loading && signals && signals.length === 0 && (
        <div className="mt-6 rounded-xl border border-gray-800 bg-gray-900 p-10 text-center text-sm text-gray-400">
          No signals found — the strategy requires all conditions to align
        </div>
      )}

      {!loading && signals && signals.length > 0 && (
        <section className="mt-6 space-y-4" aria-label="Scan results">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-gray-100">Scan Results <span className="text-sm font-normal text-gray-500">({signals.length})</span></h2>
            <span className="text-xs capitalize text-gray-500">{selectedStrategyLabel}</span>
          </div>
          {signals.map((signal, index) => {
            const key = `${signal.symbol}-${index}`;
            if (signal.error) {
              return (
                <article key={key} className="overflow-hidden rounded-xl border border-red-800 bg-red-950/40 shadow-lg">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-red-800 px-5 py-4">
                    <div className="flex items-center gap-3">
                      <h3 className="text-xl font-bold tracking-wide text-red-200">{signal.symbol}</h3>
                    </div>
                    <span className="rounded-md bg-red-600 px-4 py-1.5 text-sm font-extrabold tracking-widest text-white">ERROR</span>
                  </div>
                  <div className="p-5">
                    <p className="text-sm leading-6 text-red-300">{signal.error}</p>
                  </div>
                </article>
              );
            }
            const confidence = Number(signal.confidence);
            const isBuy = signal.action.toLowerCase() === "buy";
            return (
              <article key={key} className="overflow-hidden rounded-xl border border-gray-700 bg-gray-800 shadow-lg">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-700 px-5 py-4">
                  <div className="flex items-center gap-3"><h3 className="text-xl font-bold tracking-wide text-gray-100">{signal.symbol}</h3><span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${confidenceClass(confidence)}`}>{formatPercent(confidence)} confidence</span></div>
                  <span className={`rounded-md px-4 py-1.5 text-sm font-extrabold tracking-widest ${isBuy ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}`}>{isBuy ? "BUY" : "SELL"}</span>
                </div>
                <div className="p-5">
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <Price label="Entry Price" value={signal.entry_price} />
                    <Price label="Stop Loss" value={signal.stop_loss} />
                    <Price label="Take Profit" value={signal.take_profit} />
                    <div><p className="text-xs text-gray-500">Risk / Reward</p><p className="mt-1 text-lg font-semibold text-gray-100">{ratio(signal)}</p></div>
                  </div>
                  <blockquote className="mt-5 border-l-2 border-blue-500 bg-gray-900/60 px-4 py-3 text-sm leading-6 text-gray-300">{signal.reasoning || "No reasoning provided."}</blockquote>
                  <button type="button" onClick={() => setExpanded((previous) => ({ ...previous, [key]: !previous[key] }))} className="mt-4 text-sm font-medium text-blue-400 hover:text-blue-300">{expanded[key] ? "▾ Hide indicators" : "▸ Show indicators"}</button>
                  {expanded[key] && <div className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-gray-900 p-4 sm:grid-cols-3 md:grid-cols-4">{Object.entries(signal.indicators || {}).map(([name, value]) => <div key={name}><p className="text-xs uppercase tracking-wide text-gray-500">{name.replaceAll("_", " ")}</p><p className="mt-1 text-sm font-medium text-gray-200">{value == null ? "—" : typeof value === "number" ? value.toFixed(2) : value}</p></div>)}</div>}
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}

function Price({ label, value }: { label: string; value: number | string }) {
  return <div><p className="text-xs text-gray-500">{label}</p><p className="mt-1 text-lg font-semibold text-gray-100">{formatCurrency(value)}</p></div>;
}
