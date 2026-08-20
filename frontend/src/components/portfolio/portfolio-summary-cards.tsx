import { StatCard } from "@/components/dashboard/stat-card";
import type { PortfolioSummary } from "@/lib/portfolio-data";

function formatNpr(value: string | null): string {
  if (value === null) {
    return "—";
  }
  return `NPR ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedNpr(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${formatNpr(value)}`;
}

function formatSignedPercent(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function PortfolioSummaryCards({ summary }: { summary: PortfolioSummary }) {
  const plNum = summary.total_unrealized_pl === null ? null : Number(summary.total_unrealized_pl);
  const isUp = plNum !== null && plNum >= 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Total invested" value={formatNpr(summary.total_invested)} />
        <StatCard
          label={summary.valuation_complete ? "Current value" : "Priced value"}
          value={formatNpr(summary.valuation_complete ? summary.total_current_value : summary.priced_current_value)}
          meta={
            summary.valuation_complete
              ? undefined
              : `${summary.unpriced_holdings_count} holding${summary.unpriced_holdings_count === 1 ? "" : "s"} awaiting a quote`
          }
        />
        <StatCard
          label="Unrealized P/L"
          value={
            plNum === null ? (
              "—"
            ) : (
              <span className={isUp ? "text-success-text" : "text-danger-text"}>
                {formatSignedNpr(summary.total_unrealized_pl)}
              </span>
            )
          }
          meta={
            plNum === null ? (
              "Unavailable until all holdings have a market price"
            ) : (
              <span className={isUp ? "text-success-text" : "text-danger-text"}>
                {formatSignedPercent(summary.total_unrealized_pl_percent)}
              </span>
            )
          }
        />
      </div>

      {!summary.valuation_complete ? (
        <div className="rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-text-secondary">
          Portfolio valuation is incomplete. NPR {Number(summary.unpriced_invested_amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} of cost basis has no current quote, so total current value and P/L are intentionally withheld.
        </div>
      ) : null}
    </div>
  );
}
