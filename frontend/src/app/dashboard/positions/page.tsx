"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { formatCurrency, formatDate } from "@/lib/format";

interface Position {
  id: string;
  symbol: string;
  side: string;
  quantity: number | string;
  entry_price: number | string;
  current_price: number | string;
  status: string;
  entry_date: string;
  exit_date: string | null;
  exit_price: number | string | null;
  pnl: number | string | null;
  stop_loss: number | string | null;
  take_profit: number | string | null;
}

type Tab = "open" | "closed";

export default function PositionsPage() {
  const [tab, setTab] = useState<Tab>("open");
  const [openPositions, setOpenPositions] = useState<Position[]>([]);
  const [closedPositions, setClosedPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [closingId, setClosingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchAll = useCallback(async () => {
    setError(null);
    try {
      const [open, closed] = await Promise.all([
        apiGet<Position[]>("/api/paper/positions"),
        apiGet<Position[]>("/api/paper/positions/closed"),
      ]);
      setOpenPositions(open);
      setClosedPositions(closed);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load positions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleClose = useCallback(
    async (id: string) => {
      setClosingId(id);
      setError(null);
      try {
        await apiPost(`/api/paper/positions/${id}/close`, {});
        await fetchAll();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to close position");
      } finally {
        setClosingId(null);
      }
    },
    [fetchAll]
  );

  const hasData = openPositions.length > 0 || closedPositions.length > 0;

  return (
    <div>
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold text-gray-100">Positions</h2>
        <button
          onClick={fetchAll}
          disabled={loading}
          className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm font-medium text-gray-200 transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? "Refreshing…" : "↻ Refresh"}
        </button>
      </div>

      {/* Tabs */}
      <div className="mt-6 inline-flex rounded-lg border border-gray-700 bg-gray-800 p-1">
        <button
          onClick={() => setTab("open")}
          className={tabCls(tab === "open")}
        >
          Open ({openPositions.length})
        </button>
        <button
          onClick={() => setTab("closed")}
          className={tabCls(tab === "closed")}
        >
          Closed ({closedPositions.length})
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-amber-800 bg-amber-950/40 px-4 py-2 text-sm text-amber-300">
          {error}
        </div>
      )}

      {loading && !hasData ? (
        <div className="flex h-64 items-center justify-center">
          <div className="h-10 w-10 animate-spin rounded-full border-4 border-gray-700 border-t-blue-500" />
        </div>
      ) : tab === "open" ? (
        <OpenPositionsTable
          positions={openPositions}
          closingId={closingId}
          onClose={handleClose}
        />
      ) : (
        <ClosedPositionsTable positions={closedPositions} />
      )}
    </div>
  );
}

function tabCls(active: boolean): string {
  return active
    ? "rounded-md bg-gray-700 px-4 py-2 text-sm font-medium text-white"
    : "rounded-md px-4 py-2 text-sm font-medium text-gray-400 transition hover:text-gray-200";
}

/* ------------------------------ Open positions ----------------------------- */

function OpenPositionsTable({
  positions,
  closingId,
  onClose,
}: {
  positions: Position[];
  closingId: string | null;
  onClose: (id: string) => void;
}) {
  if (positions.length === 0) return <EmptyState message="No open positions" />;

  return (
    <div className="mt-6 overflow-x-auto rounded-xl border border-gray-700 bg-gray-800">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead className="border-b border-gray-700 text-xs uppercase tracking-wide text-gray-400">
          <tr>
            <th className="px-4 py-3">Symbol</th>
            <th className="px-4 py-3">Side</th>
            <th className="px-4 py-3 text-right">Quantity</th>
            <th className="px-4 py-3 text-right">Entry Price</th>
            <th className="px-4 py-3 text-right">Current Price</th>
            <th className="px-4 py-3 text-right">Unrealized P&L</th>
            <th className="px-4 py-3 text-right">Stop Loss</th>
            <th className="px-4 py-3 text-right">Take Profit</th>
            <th className="px-4 py-3">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/60">
          {positions.map((position) => {
            const pnl = unrealizedPnl(position);
            const isClosing = closingId === position.id;
            return (
              <tr key={position.id} className="text-gray-200">
                <td className="px-4 py-3 font-semibold text-gray-100">
                  {position.symbol}
                </td>
                <td className="px-4 py-3">
                  <SideBadge side={position.side} />
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {Number(position.quantity).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {formatCurrency(position.entry_price)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {formatCurrency(position.current_price)}
                </td>
                <td
                  className={`px-4 py-3 text-right font-medium tabular-nums ${pnlCls(pnl)}`}
                >
                  {formatCurrency(pnl)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {fmtOrDash(position.stop_loss)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {fmtOrDash(position.take_profit)}
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => onClose(position.id)}
                    disabled={isClosing}
                    className="rounded-lg bg-red-600/90 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {isClosing ? "Closing…" : "Close"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ----------------------------- Closed positions ---------------------------- */

function ClosedPositionsTable({ positions }: { positions: Position[] }) {
  if (positions.length === 0) return <EmptyState message="No closed positions" />;

  return (
    <div className="mt-6 overflow-x-auto rounded-xl border border-gray-700 bg-gray-800">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead className="border-b border-gray-700 text-xs uppercase tracking-wide text-gray-400">
          <tr>
            <th className="px-4 py-3">Symbol</th>
            <th className="px-4 py-3">Side</th>
            <th className="px-4 py-3 text-right">Quantity</th>
            <th className="px-4 py-3 text-right">Entry Price</th>
            <th className="px-4 py-3 text-right">Exit Price</th>
            <th className="px-4 py-3 text-right">Realized P&L</th>
            <th className="px-4 py-3 text-right">Entry Date</th>
            <th className="px-4 py-3 text-right">Exit Date</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-700/60">
          {positions.map((position) => {
            const realized = Number(position.pnl ?? 0);
            return (
              <tr key={position.id} className="text-gray-200">
                <td className="px-4 py-3 font-semibold text-gray-100">
                  {position.symbol}
                </td>
                <td className="px-4 py-3">
                  <SideBadge side={position.side} />
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {Number(position.quantity).toLocaleString()}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {formatCurrency(position.entry_price)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {fmtOrDash(position.exit_price)}
                </td>
                <td
                  className={`px-4 py-3 text-right font-medium tabular-nums ${pnlCls(realized)}`}
                >
                  {formatCurrency(realized)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {formatDate(position.entry_date)}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  {formatDate(position.exit_date ?? "")}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* --------------------------------- Helpers --------------------------------- */

/** Unrealized P&L: (current - entry) * qty for longs, mirrored for shorts. */
function unrealizedPnl(position: Position): number {
  const qty = Number(position.quantity);
  const entry = Number(position.entry_price);
  const current = Number(position.current_price);
  if (!Number.isFinite(qty) || !Number.isFinite(entry) || !Number.isFinite(current)) {
    return 0;
  }
  return position.side.toLowerCase() === "short"
    ? (entry - current) * qty
    : (current - entry) * qty;
}

function pnlCls(value: number): string {
  if (value > 0) return "text-emerald-400";
  if (value < 0) return "text-red-400";
  return "text-gray-300";
}

function fmtOrDash(value: number | string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  return formatCurrency(value);
}

function SideBadge({ side }: { side: string }) {
  const isShort = side.toLowerCase() === "short";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${
        isShort ? "bg-red-500/15 text-red-400" : "bg-emerald-500/15 text-emerald-400"
      }`}
    >
      {isShort ? "Short" : "Long"}
    </span>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="mt-6 rounded-xl border border-gray-800 bg-gray-900 p-12 text-center">
      <p className="text-sm text-gray-400">{message}</p>
    </div>
  );
}
