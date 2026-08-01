"use client";

export default function DashboardPage() {
  return (
    <div>
      <h2 className="text-2xl font-bold text-gray-100">Portfolio</h2>
      <p className="mt-2 text-gray-400">
        Portfolio overview coming soon — equity curve, open positions and P&amp;L
        will land in the next milestone.
      </p>
      <div className="mt-6 grid max-w-2xl grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-sm text-gray-500">Equity</p>
          <p className="mt-1 text-xl font-semibold text-gray-100">$0.00</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-sm text-gray-500">Open Positions</p>
          <p className="mt-1 text-xl font-semibold text-gray-100">0</p>
        </div>
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <p className="text-sm text-gray-500">Unrealized P&amp;L</p>
          <p className="mt-1 text-xl font-semibold text-gray-100">$0.00</p>
        </div>
      </div>
    </div>
  );
}
