export type QuantResearchPayload = {
  feature_version: string;
  status: string;
  as_of_date?: string | null;
  decision?: {
    research_label?: string;
    public_label?: string;
    public_eligible?: boolean;
    probability_outperform_nepse_after_cost?: number | null;
    confidence_score?: number;
    expected_excess_return_20d_percent?: number | null;
    horizon_trading_days?: number;
    round_trip_cost_assumption_percent?: number;
    reasons?: string[];
  };
  market_regime?: { state?: string; risk_level?: string };
  sector_regime?: { state?: string; relative_strength_vs_nepse_20d?: number | null };
  relative_strength?: {
    vs_nepse_20d_percent?: number | null;
    vs_sector_20d_percent?: number | null;
  } | null;
  liquidity?: { quality?: string; tier?: string | null };
  event_risk?: { level?: string; events?: { type?: string; date?: string; severity?: string }[] };
  confluence?: { supportive_dimensions?: number; total_dimensions?: number };
  historical_analogs?: {
    effective_sample_size?: number;
    expected_excess_return_percent?: number | null;
    downside_25th_percent?: number | null;
  };
  forward_validation?: {
    gate_status?: string;
    public_high_confidence_enabled?: boolean;
    resolved_calls?: number;
    pending_calls?: number;
  };
};

function pretty(value: string | undefined): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function probabilityPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  return `${(value * 100).toFixed(digits)}%`;
}

function percentagePoints(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(digits)}%`;
}

function tone(value: string | undefined): string {
  const normalized = (value ?? "").toLowerCase();
  if (["strong_candidate", "positive_candidate", "strong_bull", "bull", "recovery", "low", "pass"].includes(normalized)) {
    return "text-success-text";
  }
  if (["weak_candidate", "high_stress", "bear", "high", "fail", "event_risk"].includes(normalized)) {
    return "text-danger-text";
  }
  return "text-warning-text";
}

export function QuantResearchSummary({ research }: { research: QuantResearchPayload }) {
  const decision = research.decision;
  if (!decision || research.status === "insufficient_price_history") return null;

  const probability = decision.probability_outperform_nepse_after_cost;
  const validation = research.forward_validation;
  const gateOpen = Boolean(validation?.public_high_confidence_enabled);
  const analogCount = Math.round(research.historical_analogs?.effective_sample_size ?? 0);

  return (
    <section className="rounded-xl border border-border bg-card p-5 sm:p-6" aria-labelledby="quant-outlook-title">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-accent-text">20-day research outlook</p>
            <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] font-medium text-text-secondary">
              {gateOpen ? "Forward validated" : "Research only"}
            </span>
          </div>
          <h2 id="quant-outlook-title" className={`mt-2 text-2xl font-semibold ${tone(decision.research_label)}`}>
            {pretty(decision.research_label)}
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
            Probability is for outperforming NEPSE over {decision.horizon_trading_days ?? 20} trading days after a {decision.round_trip_cost_assumption_percent ?? 0.5}% cost hurdle. It is not a guarantee of profit.
          </p>
        </div>

        <div className="grid min-w-full grid-cols-2 gap-3 sm:grid-cols-4 lg:min-w-[520px]">
          <div className="rounded-lg border border-border bg-background p-3">
            <p className="text-[11px] text-text-secondary">Research probability</p>
            <p className="mt-1 text-lg font-semibold tabular-nums text-text-primary">{probabilityPercent(probability)}</p>
          </div>
          <div className="rounded-lg border border-border bg-background p-3">
            <p className="text-[11px] text-text-secondary">Evidence confidence</p>
            <p className="mt-1 text-lg font-semibold tabular-nums text-text-primary">{decision.confidence_score ?? 0}/100</p>
          </div>
          <div className="rounded-lg border border-border bg-background p-3">
            <p className="text-[11px] text-text-secondary">Expected excess</p>
            <p className="mt-1 text-lg font-semibold tabular-nums text-text-primary">
              {percentagePoints(decision.expected_excess_return_20d_percent)}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-background p-3">
            <p className="text-[11px] text-text-secondary">Confluence</p>
            <p className="mt-1 text-lg font-semibold tabular-nums text-text-primary">
              {research.confluence?.supportive_dimensions ?? 0}/{research.confluence?.total_dimensions ?? 0}
            </p>
          </div>
        </div>
      </div>

      <div className="mt-5 grid grid-cols-2 gap-3 border-t border-border pt-5 md:grid-cols-4">
        <div>
          <p className="text-xs text-text-secondary">NEPSE regime</p>
          <p className={`mt-1 text-sm font-semibold ${tone(research.market_regime?.state)}`}>{pretty(research.market_regime?.state)}</p>
        </div>
        <div>
          <p className="text-xs text-text-secondary">Sector regime</p>
          <p className={`mt-1 text-sm font-semibold ${tone(research.sector_regime?.state)}`}>{pretty(research.sector_regime?.state)}</p>
        </div>
        <div>
          <p className="text-xs text-text-secondary">Relative strength vs NEPSE</p>
          <p className="mt-1 text-sm font-semibold text-text-primary">{percentagePoints(research.relative_strength?.vs_nepse_20d_percent)}</p>
        </div>
        <div>
          <p className="text-xs text-text-secondary">Event risk</p>
          <p className={`mt-1 text-sm font-semibold ${tone(research.event_risk?.level)}`}>{pretty(research.event_risk?.level)}</p>
        </div>
      </div>

      <details className="mt-5 border-t border-border pt-4">
        <summary className="cursor-pointer text-sm font-medium text-text-primary">Why this research view?</summary>
        <div className="mt-3 grid gap-4 text-sm text-text-secondary lg:grid-cols-2">
          <div>
            <p className="font-medium text-text-primary">Decision evidence</p>
            <ul className="mt-2 space-y-2">
              {(decision.reasons ?? []).map((reason) => <li key={reason}>• {reason}</li>)}
              <li>• Historical analogue effective sample: {analogCount || "insufficient"}</li>
              <li>• 25th-percentile analogue excess return: {percentagePoints(research.historical_analogs?.downside_25th_percent)}</li>
            </ul>
          </div>
          <div>
            <p className="font-medium text-text-primary">Validation status</p>
            <ul className="mt-2 space-y-2">
              <li>• Gate: {pretty(validation?.gate_status)}</li>
              <li>• Resolved shadow calls: {validation?.resolved_calls ?? 0}</li>
              <li>• Pending shadow calls: {validation?.pending_calls ?? 0}</li>
              <li>• Public high-confidence wording stays disabled until the precommitted gate passes.</li>
            </ul>
          </div>
        </div>
      </details>
    </section>
  );
}
