import { TrendingDown, TrendingUp } from "lucide-react";
import { LivePriceBadge } from "@/components/stock/live-price-badge";
import { WatchlistToggleButton } from "@/components/stock/watchlist-toggle-button";
import type { StockSummary } from "@/lib/market-data";

function formatPrice(value: string): string {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatPercent(value: string | null): string {
  if (value === null) {
    return "0.00%";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function StockHeader({
  summary,
  isWatching,
  isAuthenticated,
}: {
  summary: StockSummary;
  isWatching: boolean;
  isAuthenticated: boolean;
}) {
  const change = Number(summary.percent_change ?? 0);
  const isUp = change >= 0;

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 sm:flex-row sm:items-start sm:justify-between">
      <div>
        <div className="flex flex-wrap items-baseline gap-2">
          <h1 className="text-2xl font-semibold text-text-primary">{summary.symbol}</h1>
          <span className="text-sm text-text-secondary">{summary.company_name}</span>
          <WatchlistToggleButton symbol={summary.symbol} initialWatching={isWatching} isAuthenticated={isAuthenticated} />
        </div>
        <p className="mt-1 text-sm text-text-secondary">{summary.sector}</p>
      </div>

      <div className="flex flex-col items-start gap-2 sm:items-end">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold text-text-primary">Rs {formatPrice(summary.latest_close)}</span>
          <span
            className={`flex items-center gap-1 rounded-md px-2 py-0.5 text-sm font-medium ${
              isUp ? "bg-success/10 text-success-text" : "bg-danger/10 text-danger-text"
            }`}
          >
            {isUp ? <TrendingUp className="size-4" aria-hidden="true" /> : <TrendingDown className="size-4" aria-hidden="true" />}
            {formatPercent(summary.percent_change)}
          </span>
        </div>
        <LivePriceBadge symbol={summary.symbol} />
        <div className="flex gap-4 text-xs text-text-secondary">
          <span>
            Day high <span className="text-text-primary">{formatPrice(summary.day_high)}</span>
          </span>
          <span>
            Day low <span className="text-text-primary">{formatPrice(summary.day_low)}</span>
          </span>
        </div>
      </div>
    </div>
  );
}
