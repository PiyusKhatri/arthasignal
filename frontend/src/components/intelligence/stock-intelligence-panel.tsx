import { AiAnalystCard } from "@/components/intelligence/ai-analyst-card";
import { ArthaScoreCard } from "@/components/intelligence/artha-score-card";
import { IntelligenceBreakdown } from "@/components/intelligence/intelligence-breakdown";
import type { StockIntelligence } from "@/lib/market-data";

type IntelligenceView = StockIntelligence & {
  confidence_score?: number;
  market_regime?: {
    state?: string;
    risk_level?: string;
    confidence?: number;
    return_20d_percent?: number | null;
    drawdown_percent?: number | null;
    annualized_volatility_percent?: number | null;
  };
  forward_validation?: {
    state?: string;
    policy_version?: string | null;
  };
  evidence?: {
    scope?: string;
    min_sample_size?: number;
    average_edge_vs_baseline?: number | null;
    weighted_win_rate?: number | null;
    weighted_average_return?: number | null;
  };
};

function prettyLabel(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "N/A";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function asPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "N/A";
  const normalized = Math.abs(value) <= 1 ? value * 100 : value;
  return `${normalized.toFixed(1)}%`;
}

function technicalTone(value: string): string {
  const normalized = value.toLowerCase();
  if (["above", "bullish", "healthy", "strong", "recovery", "low"].includes(normalized)) return "text-success-text";
  if (["below", "bearish", "overbought", "weak", "high", "failed"].includes(normalized)) return "text-danger-text";
  if (["mixed", "medium", "collecting", "neutral"].includes(normalized)) return "text-warning-text";
  return "text-text-primary";
}

function summaryTone(value: string): string {
  const normalized = value.toLowerCase();
  if (["strong_setup", "positive_setup", "bullish", "recovery", "low"].includes(normalized)) {
    return "text-success-text";
  }
  if (["weak", "bearish", "high", "failed"].includes(normalized)) return "text-danger-text";
  return "text-warning-text";
}

export function StockIntelligencePanel({ intelligence }: { intelligence: IntelligenceView }) {
  const marketState = intelligence.market_regime?.state ?? "unavailable";
  const confidenceScore = intelligence.confidence_score;
  const validationState = intelligence.forward_validation?.state ?? "unavailable";
  const topStrengths = intelligence.strengths.slice(0, 3);
  const topWarnings = intelligence.risk.warnings.slice(0, 3);
  const activeSignal = intelligence.signals[0]?.signal_name;

  const technicalHealth = [
    { label: "RSI (14)", value: formatNumber(intelligence.technical.rsi), state: intelligence.technical.rsi_state },
    { label: "MACD", value: prettyLabel(intelligence.technical.macd), state: intelligence.technical.macd },
    {
      label: "Price vs SMA200",
      value: prettyLabel(intelligence.technical.price_vs_sma_200),
      state: intelligence.technical.price_vs_sma_200,
    },
    {
      label: "SMA50 vs SMA200",
      value: prettyLabel(intelligence.technical.sma_50_vs_sma_200),
      state: intelligence.technical.sma_50_vs_sma_200,
    },
    {
      label: "Liquidity",
      value: prettyLabel(intelligence.liquidity.tier),
      state: intelligence.liquidity.score >= 7 ? "healthy" : "weak",
    },
    {
      label: "Signal quality",
      value: prettyLabel(intelligence.signal_quality),
      state:
        intelligence.signal_quality === "high"
          ? "healthy"
          : intelligence.signal_quality === "low"
            ? "weak"
            : "neutral",
    },
  ];

  return (
    <section aria-labelledby="artha-intelligence-title" className="space-y-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-accent-text">Artha view</p>
          <h2 id="artha-intelligence-title" className="mt-1 text-xl font-semibold text-text-primary">
            What ArthaSignal sees right now
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-text-secondary">
            A simple decision view first. Open the detailed analysis only when you want the quantitative evidence underneath it.
          </p>
        </div>
        <div className="text-xs text-text-secondary">
          {intelligence.as_of_date ? `Updated ${intelligence.as_of_date}` : "Latest snapshot unavailable"}
        </div>
      </div>

      <ArthaScoreCard intelligence={intelligence} />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Decision summary">
        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-text-secondary">Current view</p>
          <p className={`mt-2 text-base font-semibold ${summaryTone(intelligence.rating)}`}>
            {prettyLabel(intelligence.rating)}
          </p>
          {activeSignal ? <p className="mt-1 text-xs text-text-secondary">{prettyLabel(activeSignal)}</p> : null}
        </article>

        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-text-secondary">Evidence confidence</p>
          <p className="mt-2 text-base font-semibold text-text-primary">
            {typeof confidenceScore === "number" ? `${confidenceScore}/100` : prettyLabel(intelligence.confidence_level)}
          </p>
          <p className="mt-1 text-xs text-text-secondary">{prettyLabel(intelligence.confidence_level)}</p>
        </article>

        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-text-secondary">NEPSE environment</p>
          <p className={`mt-2 text-base font-semibold ${summaryTone(marketState)}`}>{prettyLabel(marketState)}</p>
          {intelligence.market_regime?.return_20d_percent !== undefined ? (
            <p className="mt-1 text-xs text-text-secondary">20D {asPercent(intelligence.market_regime.return_20d_percent)}</p>
          ) : null}
        </article>

        <article className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs text-text-secondary">Risk</p>
          <p className={`mt-2 text-base font-semibold ${summaryTone(intelligence.risk.level)}`}>
            {prettyLabel(intelligence.risk.level)}
          </p>
          <p className="mt-1 text-xs text-text-secondary">Validation: {prettyLabel(validationState)}</p>
        </article>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2" aria-label="Key reasons">
        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-success-text">Why it looks good</p>
          <ul className="mt-4 space-y-3">
            {topStrengths.length > 0 ? (
              topStrengths.map((item) => (
                <li key={item} className="flex gap-3 text-sm leading-relaxed text-text-primary">
                  <span className="mt-0.5 text-success-text" aria-hidden="true">✓</span>
                  <span>{item}</span>
                </li>
              ))
            ) : (
              <li className="text-sm text-text-secondary">No strong positive evidence is confirmed right now.</li>
            )}
          </ul>
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-danger-text">What to watch</p>
          <ul className="mt-4 space-y-3">
            {topWarnings.length > 0 ? (
              topWarnings.map((item) => (
                <li key={item} className="flex gap-3 text-sm leading-relaxed text-text-primary">
                  <span className="mt-0.5 text-danger-text" aria-hidden="true">!</span>
                  <span>{item}</span>
                </li>
              ))
            ) : (
              <li className="text-sm text-text-secondary">No elevated risk warning is active in the current snapshot.</li>
            )}
          </ul>
        </article>
      </section>

      {intelligence.ai_analysis?.summary ? (
        <article className="rounded-xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">In plain English</p>
              <h3 className="mt-1 text-base font-semibold text-text-primary">Why ArthaSignal reached this view</h3>
            </div>
            <span className="rounded-md border border-border bg-background px-2 py-1 text-[11px] text-text-secondary">
              Explanation only
            </span>
          </div>
          <p className="mt-3 max-w-4xl text-sm leading-6 text-text-primary">{intelligence.ai_analysis.summary}</p>
        </article>
      ) : null}

      <details className="group rounded-xl border border-border bg-card">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 p-5 marker:hidden">
          <div>
            <p className="text-sm font-semibold text-text-primary">View detailed analysis</p>
            <p className="mt-1 text-xs text-text-secondary">
              Score pillars, technical indicators, backtests, validation evidence, and methodology.
            </p>
          </div>
          <span className="text-lg text-text-secondary transition-transform group-open:rotate-45" aria-hidden="true">+</span>
        </summary>

        <div className="space-y-5 border-t border-border p-5">
          <IntelligenceBreakdown scores={intelligence.scores} />

          <article className="rounded-xl border border-border bg-background p-5">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Technical detail</p>
              <h3 className="mt-1 text-base font-semibold text-text-primary">Latest confirmation matrix</h3>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
              {technicalHealth.map((item) => (
                <div key={item.label} className="rounded-lg border border-border bg-card p-3">
                  <p className="text-xs text-text-secondary">{item.label}</p>
                  <p className={`mt-1 text-sm font-semibold ${technicalTone(item.state)}`}>{item.value}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 text-xs text-text-secondary sm:grid-cols-3">
              <p>Latest price: <span className="font-medium text-text-primary">{formatNumber(intelligence.technical.latest_price)}</span></p>
              <p>SMA50: <span className="font-medium text-text-primary">{formatNumber(intelligence.technical.sma_50)}</span></p>
              <p>SMA200: <span className="font-medium text-text-primary">{formatNumber(intelligence.technical.sma_200)}</span></p>
            </div>
          </article>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <article className="rounded-xl border border-border bg-background p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Evidence quality</p>
                  <h3 className="mt-1 text-base font-semibold text-text-primary">Historical edge & validation</h3>
                </div>
                <span className="rounded-md border border-border bg-card px-2 py-1 text-xs font-medium text-text-primary">
                  {prettyLabel(intelligence.confidence_level)}
                </span>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                <div className="rounded-lg border border-border bg-card p-3">
                  <p className="text-xs text-text-secondary">Forward validation</p>
                  <p className={`mt-1 font-semibold ${technicalTone(validationState)}`}>{prettyLabel(validationState)}</p>
                </div>
                <div className="rounded-lg border border-border bg-card p-3">
                  <p className="text-xs text-text-secondary">Historical sample</p>
                  <p className="mt-1 font-semibold text-text-primary">{intelligence.evidence?.min_sample_size ?? 0}</p>
                </div>
                <div className="rounded-lg border border-border bg-card p-3">
                  <p className="text-xs text-text-secondary">Edge vs baseline</p>
                  <p className="mt-1 font-semibold text-text-primary">{asPercent(intelligence.evidence?.average_edge_vs_baseline)}</p>
                </div>
                <div className="rounded-lg border border-border bg-card p-3">
                  <p className="text-xs text-text-secondary">Avg forward return</p>
                  <p className="mt-1 font-semibold text-text-primary">{asPercent(intelligence.evidence?.weighted_average_return)}</p>
                </div>
              </div>

              {intelligence.confidence.length > 0 ? (
                <div className="mt-4 space-y-3">
                  {intelligence.confidence.map((item) => (
                    <div key={item.signal_name} className="rounded-lg border border-border bg-card p-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <p className="text-sm font-medium text-text-primary">{prettyLabel(item.signal_name)}</p>
                        <span className="text-xs font-medium text-accent-text">{prettyLabel(item.tier)}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-text-secondary">
                        <span>Edge <strong className="font-medium text-text-primary">{asPercent(item.edge_vs_baseline)}</strong></span>
                        <span>Min sample <strong className="font-medium text-text-primary">{item.min_sample_size}</strong></span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>

            <article className="rounded-xl border border-border bg-background p-5">
              <div>
                <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Historical evidence</p>
                <h3 className="mt-1 text-base font-semibold text-text-primary">Market-wide signal backtests</h3>
              </div>

              {intelligence.backtest_summary.length > 0 ? (
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[520px] text-left text-sm">
                    <thead className="border-b border-border text-xs text-text-secondary">
                      <tr>
                        <th className="pb-2 pr-3 font-medium">Signal</th>
                        <th className="pb-2 pr-3 font-medium">Window</th>
                        <th className="pb-2 pr-3 font-medium">Win rate</th>
                        <th className="pb-2 pr-3 font-medium">Avg return</th>
                        <th className="pb-2 font-medium">Sample</th>
                      </tr>
                    </thead>
                    <tbody>
                      {intelligence.backtest_summary.map((row) => (
                        <tr key={`${row.signal_name}-${row.forward_days}`} className="border-b border-border/60 last:border-0">
                          <td className="py-3 pr-3 font-medium text-text-primary">{prettyLabel(row.signal_name)}</td>
                          <td className="py-3 pr-3 text-text-secondary">{row.forward_days}d</td>
                          <td className="py-3 pr-3 text-text-primary">{asPercent(row.win_rate)}</td>
                          <td className="py-3 pr-3 text-text-primary">{asPercent(row.average_return)}</td>
                          <td className="py-3 text-text-secondary">{row.sample_size}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-3 text-xs leading-relaxed text-text-secondary">
                    These results describe market-wide signal behavior, not a stock-specific probability of profit.
                  </p>
                </div>
              ) : (
                <p className="mt-4 rounded-lg border border-border bg-card p-4 text-sm text-text-secondary">
                  No historical backtest row matches the stock&apos;s current signal state.
                </p>
              )}
            </article>
          </div>

          {intelligence.ai_analysis ? <AiAnalystCard intelligence={intelligence} /> : null}

          <article className="rounded-xl border border-border bg-background p-5">
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Method notes</p>
            <ul className="mt-3 space-y-2 text-sm text-text-secondary">
              {intelligence.explanation.map((item) => (
                <li key={item} className="border-l-2 border-accent-primary-light pl-3">{item}</li>
              ))}
            </ul>
          </article>
        </div>
      </details>
    </section>
  );
}
