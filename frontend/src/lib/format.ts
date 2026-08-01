/**
 * Shared formatting helpers for the dashboard.
 *
 * The backend serializes Decimal fields as numbers (or occasionally strings),
 * so every helper accepts both and coerces through Number().
 */

/** "$1,234.56" */
export function formatCurrency(value: number | string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "$0.00";
  return num.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** "+12.34%" or "-5.67%" */
export function formatPercent(value: number | string): string {
  const num = Number(value);
  if (!Number.isFinite(num)) return "0.00%";
  const abs = Math.abs(num).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (num > 0) return `+${abs}%`;
  if (num < 0) return `-${abs}%`;
  return `${abs}%`;
}

/** "Jul 31, 2026" */
export function formatDate(isoString: string): string {
  if (!isoString) return "—";
  const date = new Date(isoString);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
