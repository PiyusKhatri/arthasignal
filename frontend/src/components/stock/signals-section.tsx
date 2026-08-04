import type { StockSignal } from "@/lib/market-data";
import { isHighConfidenceTier, tierLabel } from "@/lib/signal-tiers";

function formatEdge(value: string | null): string {
  if (value === null) {
    return "No edge data";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)} pts win rate vs. baseline`;
}

function SignalCard({ signal }: { signal: StockSignal }) {
  const isHighConfidence = isHighConfidenceTier(signal.tier);

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-semibold text-text-primary">{signal.signal_name}</p>
        <span
          className={`rounded-md px-2 py-0.5 text-xs font-medium ${
            isHighConfidence ? "bg-success/10 text-success-text" : "bg-warning/10 text-warning-text"
          }`}
        >
          {tierLabel(signal.tier)}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm text-text-primary">
        <span>{formatEdge(signal.avg_win_rate_minus_baseline)}</span>
        {signal.recommended_holding_period ? (
          <span className="text-text-secondary">Hold: {signal.recommended_holding_period}</span>
        ) : null}
      </div>

      {signal.cost_viability_note ? (
        <p className="mt-2 text-xs text-text-secondary">{signal.cost_viability_note}</p>
      ) : null}
    </div>
  );
}

export function SignalsSection({ signals }: { signals: StockSignal[] }) {
  const activeSignals = signals.filter((signal) => signal.active);

  if (activeSignals.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
        No signal is currently active for this symbol. Edges shown here are backtested and honest about their
        limits — they only appear when the underlying condition actually triggers.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {activeSignals.map((signal) => (
        <SignalCard key={signal.signal_name} signal={signal} />
      ))}
    </div>
  );
}
