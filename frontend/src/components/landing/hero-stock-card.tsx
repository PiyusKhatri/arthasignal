import { TrendingDown, TrendingUp } from "lucide-react";
import type { StockSignals, StockSummary } from "@/lib/landing-data";

function formatPercent(value: string | null): string {
  if (value === null) {
    return "0.00%";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function formatPrice(value: string): string {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function HeroStockCard({ summary, signals }: { summary: StockSummary; signals: StockSignals }) {
  const change = Number(summary.percent_change ?? 0);
  const isUp = change >= 0;
  const activeSignal = signals.signals.find((signal) => signal.active);

  return (
    <div className="w-full max-w-sm rounded-lg border border-border bg-card p-6">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-text-secondary">{summary.sector}</p>
          <p className="text-lg font-semibold text-text-primary">{summary.symbol}</p>
          <p className="text-sm text-text-secondary">{summary.company_name}</p>
        </div>
        <span
          className={`flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium ${
            isUp ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
          }`}
        >
          {isUp ? <TrendingUp className="size-4" aria-hidden="true" /> : <TrendingDown className="size-4" aria-hidden="true" />}
          {formatPercent(summary.percent_change)}
        </span>
      </div>

      <p className="mt-4 text-3xl font-semibold tracking-tight text-text-primary">
        Rs {formatPrice(summary.latest_close)}
      </p>
      <p className="text-xs text-text-secondary">as of {summary.latest_date}</p>

      <div className="mt-5 border-t border-border pt-4">
        {activeSignal ? (
          <div>
            <p className="text-sm font-medium text-text-primary">{activeSignal.signal_name}</p>
            <p className="mt-1 text-xs text-text-secondary">
              Historically {activeSignal.avg_win_rate_minus_baseline}pp above baseline win rate, held{" "}
              {activeSignal.recommended_holding_period?.toLowerCase()}.
            </p>
          </div>
        ) : (
          <p className="text-sm text-text-secondary">No tracked signal is active for {summary.symbol} right now.</p>
        )}
      </div>
    </div>
  );
}
