import type { StockSignal } from "@/lib/market-data";
import { plainLanguageSignalLabel } from "@/lib/signal-labels";
import { NEUTRAL_TIER_CLASS, signalStatusLabel, signalStatusTitle } from "@/lib/signal-tiers";

function formatEdge(value: string | null): string {
  if (value === null) {
    return "No earlier backtest data";
  }
  const num = Number(value);
  return `Earlier backtest, not re-validated: ${num >= 0 ? "+" : ""}${num.toFixed(2)} pts win rate vs. baseline`;
}

function SignalCard({ signal }: { signal: StockSignal }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-text-primary">{plainLanguageSignalLabel(signal.signal_name)}</p>
          <p className="mt-0.5 text-xs text-text-secondary">{signal.signal_name}</p>
        </div>
        <span
          title={signalStatusTitle(signal.signal_name, signal.validation)}
          className={`rounded-md px-2 py-0.5 text-xs font-medium ${NEUTRAL_TIER_CLASS}`}
        >
          {signalStatusLabel(signal.tier, signal.validation)}
        </span>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm text-text-primary">
        <span className="text-text-secondary">{formatEdge(signal.avg_win_rate_minus_baseline)}</span>
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
        No signal is currently active for this symbol. Signals are under validation on live paper trades and
        only appear when the underlying condition actually triggers.
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
