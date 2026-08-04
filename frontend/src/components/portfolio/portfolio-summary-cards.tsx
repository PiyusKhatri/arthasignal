import { StatCard } from "@/components/dashboard/stat-card";
import type { PortfolioSummary } from "@/lib/portfolio-data";

function formatNpr(value: string): string {
  return `NPR ${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSignedNpr(value: string): string {
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
  const plNum = Number(summary.total_unrealized_pl);
  const isUp = plNum >= 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <StatCard label="Total invested" value={formatNpr(summary.total_invested)} />
      <StatCard label="Current value" value={formatNpr(summary.total_current_value)} />
      <StatCard
        label="Unrealized P/L"
        value={
          <span className={isUp ? "text-success-text" : "text-danger-text"}>
            {formatSignedNpr(summary.total_unrealized_pl)}
          </span>
        }
        meta={
          <span className={isUp ? "text-success-text" : "text-danger-text"}>
            {formatSignedPercent(summary.total_unrealized_pl_percent)}
          </span>
        }
      />
    </div>
  );
}
