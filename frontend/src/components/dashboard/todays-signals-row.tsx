import { TrendingDown, TrendingUp } from "lucide-react";
import type { ActiveSignal } from "@/lib/market-data";
import { plainLanguageSignalLabel } from "@/lib/signal-labels";

function formatPercent(value: string | null): string {
  if (value === null) {
    return "0.00%";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function SignalCard({ signal }: { signal: ActiveSignal }) {
  const change = Number(signal.percent_change ?? 0);
  const isUp = change >= 0;
  const isHighConfidence = signal.tier === "high_confidence";

  return (
    <div className="w-64 shrink-0 snap-start rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-semibold text-text-primary">{signal.symbol}</p>
          <p className="text-xs text-text-secondary">{signal.sector}</p>
        </div>
        <span
          className={`flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-medium ${
            isUp ? "bg-success/10 text-success-text" : "bg-danger/10 text-danger-text"
          }`}
        >
          {isUp ? <TrendingUp className="size-3.5" aria-hidden="true" /> : <TrendingDown className="size-3.5" aria-hidden="true" />}
          {formatPercent(signal.percent_change)}
        </span>
      </div>

      <p className="mt-3 text-lg font-semibold text-text-primary">Rs {Number(signal.latest_close).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>

      <div className="mt-3 border-t border-border pt-3">
        <p className="text-sm text-text-primary">{plainLanguageSignalLabel(signal.signal_name)}</p>
        <p className="mt-0.5 text-xs text-text-secondary">{signal.signal_name}</p>
        <span
          className={`mt-2 inline-block rounded-md px-2 py-0.5 text-xs font-medium ${
            isHighConfidence ? "bg-accent-text/10 text-accent-text" : "bg-border text-text-secondary"
          }`}
        >
          {(signal.tier ?? "unrated").replace(/_/g, " ")}
        </span>
      </div>
    </div>
  );
}

export function TodaysSignalsRow({ signals }: { signals: ActiveSignal[] }) {
  if (signals.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
        No tracked signal is active across any symbol right now.
      </div>
    );
  }

  return (
    <div className="flex snap-x gap-4 overflow-x-auto pb-2">
      {signals.map((signal) => (
        <SignalCard key={`${signal.symbol}-${signal.signal_name}`} signal={signal} />
      ))}
    </div>
  );
}
