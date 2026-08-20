import { AiAnalystCard } from "@/components/intelligence/ai-analyst-card";
import { ArthaScoreCard } from "@/components/intelligence/artha-score-card";
import { IntelligenceBreakdown } from "@/components/intelligence/intelligence-breakdown";
import type { StockIntelligence } from "@/lib/market-data";

function prettyLabel(value: string | null): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatNumber(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "N/A";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function asPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) return "N/A";
  const normalized = Math.abs(value) <= 1 ? value * 100 : value;
  return `${normalized.toFixed(1)}%`;
}

function technicalTone(value: string): string {
  const normalized = value.toLowerCase();
  if (["above", "bullish", "healthy", "strong"].includes(normalized)) return "text-success-text";
  if (["below", "bearish", "overbought", "weak"].includes(normalized)) return "text-danger-text";
  return "text-text-primary";
}

export function StockIntelligencePanel({ intelligence }: { intelligence: StockIntelligence }) {
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
    { label: "Liquidity", value: prettyLabel(intelligence.liquidity.tier), state: intelligence.liquidity.score >= 7 ? "healthy" : "weak" },
    { label: "Signal quality", value: prettyLabel(intelligence.signal_quality), state: intelligence.signal_quality === "high" ? "healthy" : intelligence.signal_quality === "low" ? "weak" : "neutral" },
  ];

  return (
    <section aria-labelledby="artha-intelligence-title" className="space-y-5">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-accent-text">Decision layer</p>
          <h2 id="artha-intelligence-title" className="mt-1 text-xl font-semibold text-text-primary">Artha Intelligence v2</h2>
          <p className="mt-1 max-w-2xl text-sm text-text-secondary">
            Evidence-weighted setup scoring using market-wide signal backtests, forward-validation status, NEPSE regime context, liquidity, and fundamentals for {intelligence.company_name}.
          </p>
        </div>
        <div className="text-xs text-text-secondary">
          {intelligence.as_of_date ? `Technical snapshot: ${intelligence.as_of_date}` : "Latest technical snapshot unavailable"}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <ArthaScoreCard intelligence={intelligence} />
        <IntelligenceBreakdown scores={intelligence.scores} />
      </div>

      {intelligence.ai_analysis && (
        <AiAnalystCard intelligence={intelligence} />
      )}

      <article className="rounded-xl border border-border bg-card p-5">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Technical health</p>
          <h3 className="mt-1 text-base font-semibold text-text-primary">Latest confirmation matrix</h3>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
          {technicalHealth.map((item) => (
            <div key={item.label} className="rounded-lg border border-border bg-background p-3">
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
        <article className="rounded-xl border border-border bg-card p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Evidence confidence</p>
              <h3 className="mt-1 text-base font-semibold text-text-primary">Historical edge & validation evidence</h3>
            </div>
            <span className="rounded-md border border-border bg-background px-2 py-1 text-xs font-medium text-text-primary">
              {prettyLabel(intelligence.confidence_level)} confidence
            </span>
          </div>

          {intelligence.confidence.length > 0 ? (
            <div className="mt-4 space-y-3">
              {intelligence.confidence.map((item) => (
                <div key={item.signal_name} className="rounded-lg border border-border bg-background p-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="text-sm font-medium text-text-primary">{prettyLabel(item.signal_name)}</p>
                    <span className="text-xs font-medium text-accent-text">{prettyLabel(item.tier)}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-text-secondary">
                    <span>Edge vs baseline <strong className="font-medium text-text-primary">{asPercent(item.edge_vs_baseline)}</strong></span>
                    <span>Min sample <strong className="font-medium text-text-primary">{item.min_sample_size}</strong></span>
                  </div>
                  {item.recommended_holding_period ? (
                    <p className="mt-2 text-xs text-text-secondary">Holding window: {item.recommended_holding_period}</p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-4 rounded-lg border border-border bg-background p-4 text-sm text-text-secondary">
              No active validated signal currently has a historical confidence record. Reliability contributes zero points until evidence is available.
            </p>
          )}
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Historical validation</p>
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
                These rows describe how the signal behaved across the historical market universe; they are not stock-specific probability estimates.
              </p>
            </div>
          ) : (
            <p className="mt-4 rounded-lg border border-border bg-background p-4 text-sm text-text-secondary">
              No market-wide historical backtest rows match the stock&apos;s current validated signal set.
            </p>
          )}
        </article>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Strengths</p>
          <ul className="mt-3 space-y-2 text-sm text-text-primary">
            {intelligence.strengths.length > 0 ? intelligence.strengths.map((item) => (
              <li key={item} className="rounded-lg bg-success/10 px-3 py-2">{item}</li>
            )) : <li className="text-text-secondary">No material strength has been confirmed yet.</li>}
          </ul>
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Risk warnings</p>
          <ul className="mt-3 space-y-2 text-sm text-text-primary">
            {intelligence.risk.warnings.length > 0 ? intelligence.risk.warnings.map((item) => (
              <li key={item} className="rounded-lg bg-danger/10 px-3 py-2">{item}</li>
            )) : <li className="rounded-lg bg-success/10 px-3 py-2">No elevated technical risk flag is active.</li>}
          </ul>
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Why this score</p>
          <ul className="mt-3 space-y-2 text-sm text-text-secondary">
            {intelligence.explanation.map((item) => (
              <li key={item} className="border-l-2 border-accent-primary-light pl-3">{item}</li>
            ))}
          </ul>
        </article>
      </div>
    </section>
  );
}
