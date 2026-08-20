import Link from "next/link";
import type { MarketIntelligence } from "@/lib/market-data";

function prettyLabel(value: string | null): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function percent(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function conditionTone(value: string): string {
  const normalized = value.toLowerCase();
  if (["bullish", "constructive", "low"].includes(normalized)) return "text-success-text";
  if (["mixed", "neutral", "medium"].includes(normalized)) return "text-warning-text";
  if (["bearish", "defensive", "high"].includes(normalized)) return "text-danger-text";
  return "text-text-primary";
}

function ScoreBadge({ score }: { score: number }) {
  const tone = score >= 70 ? "bg-success/10 text-success-text" : score >= 50 ? "bg-warning/10 text-warning-text" : "bg-danger/10 text-danger-text";
  return <span className={`rounded-md px-2 py-1 text-xs font-semibold tabular-nums ${tone}`}>{score}</span>;
}

export function MarketIntelligenceOverview({ intelligence }: { intelligence: MarketIntelligence }) {
  const summaryCards = [
    { label: "Market condition", value: prettyLabel(intelligence.market.condition), tone: conditionTone(intelligence.market.condition) },
    { label: "Trend", value: prettyLabel(intelligence.market.trend), tone: conditionTone(intelligence.market.trend) },
    { label: "Risk level", value: prettyLabel(intelligence.market.risk_level), tone: conditionTone(intelligence.market.risk_level) },
    { label: "Average Artha Score", value: intelligence.market.average_artha_score.toFixed(1), tone: "text-text-primary" },
  ];

  return (
    <div className="space-y-6">
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {summaryCards.map((card) => (
          <article key={card.label} className="rounded-xl border border-border bg-card p-4">
            <p className="text-xs text-text-secondary">{card.label}</p>
            <p className={`mt-2 text-xl font-semibold ${card.tone}`}>{card.value}</p>
          </article>
        ))}
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <article className="rounded-xl border border-border bg-card p-5 xl:col-span-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Opportunity ranking</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Qualified Artha setups</h2>
            </div>
            <p className="text-xs text-text-secondary">{intelligence.market.stocks_analyzed} stocks analyzed</p>
          </div>

          {intelligence.top_opportunities.length > 0 ? (
            <div className="mt-4 divide-y divide-border">
              {intelligence.top_opportunities.map((stock, index) => (
                <Link
                  key={stock.symbol}
                  href={`/stock/${stock.symbol}`}
                  className="grid grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-3 py-3 transition-opacity hover:opacity-80"
                >
                  <span className="text-xs font-medium text-text-secondary">{String(index + 1).padStart(2, "0")}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-semibold text-text-primary">{stock.symbol}</span>
                      <span className="truncate text-xs text-text-secondary">{stock.company_name}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-text-secondary">
                      <span>{stock.sector ?? "Other"}</span>
                      <span>{prettyLabel(stock.trend_strength)} trend</span>
                      <span>{prettyLabel(stock.confidence_level)} confidence</span>
                    </div>
                  </div>
                  <ScoreBadge score={stock.artha_score} />
                </Link>
              ))}
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-border bg-background p-4">
              <p className="text-sm font-medium text-text-primary">No setup currently clears the evidence gate.</p>
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                The ranking now requires a signal that is active in the latest technical snapshot plus sufficient score and evidence confidence. An empty list is a valid result.
              </p>
            </div>
          )}
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Market quality</p>
          <h2 className="mt-1 text-lg font-semibold text-text-primary">Signal participation</h2>

          <dl className="mt-5 space-y-4">
            <div>
              <div className="flex items-center justify-between text-sm">
                <dt className="text-text-secondary">Positive setups</dt>
                <dd className="font-semibold text-text-primary">{percent(intelligence.market.positive_setup_share)}</dd>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background">
                <div className="h-full rounded-full bg-accent-primary-light" style={{ width: `${intelligence.market.positive_setup_share * 100}%` }} />
              </div>
            </div>
            <div>
              <div className="flex items-center justify-between text-sm">
                <dt className="text-text-secondary">High-confidence signals</dt>
                <dd className="font-semibold text-text-primary">{percent(intelligence.market.high_confidence_share)}</dd>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background">
                <div className="h-full rounded-full bg-accent-primary-light" style={{ width: `${intelligence.market.high_confidence_share * 100}%` }} />
              </div>
            </div>
          </dl>

          <div className="mt-6 border-t border-border pt-4">
            <p className="text-xs text-text-secondary">As of</p>
            <p className="mt-1 text-sm font-medium text-text-primary">{intelligence.as_of_date ?? "Latest available session"}</p>
          </div>
        </article>
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <article className="rounded-xl border border-border bg-card p-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Validated signals</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">High-confidence opportunities</h2>
          </div>

          {intelligence.high_confidence_signals.length > 0 ? (
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {intelligence.high_confidence_signals.map((stock) => (
                <Link key={stock.symbol} href={`/stock/${stock.symbol}`} className="rounded-lg border border-border bg-background p-3 hover:border-accent-primary-light">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-text-primary">{stock.symbol}</p>
                      <p className="mt-0.5 text-xs text-text-secondary">{stock.sector ?? "Other"}</p>
                    </div>
                    <ScoreBadge score={stock.artha_score} />
                  </div>
                  <p className="mt-3 line-clamp-2 text-xs text-text-secondary">
                    {stock.active_signals.length > 0 ? stock.active_signals.map(prettyLabel).join(" · ") : "Validated signal available"}
                  </p>
                </Link>
              ))}
            </div>
          ) : (
            <p className="mt-4 rounded-lg border border-border bg-background p-4 text-sm text-text-secondary">
              No active signal currently meets the high-confidence threshold. This is a valid market state, not missing data.
            </p>
          )}
        </article>

        <article className="rounded-xl border border-border bg-card p-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Sector intelligence</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">Where strength is concentrated</h2>
          </div>

          <div className="mt-4 divide-y divide-border">
            {intelligence.sector_insights.slice(0, 8).map((sector) => (
              <div key={sector.sector} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 py-3">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-text-primary">{sector.sector}</p>
                  <p className="mt-1 text-xs text-text-secondary">
                    Top: {sector.top_symbol} ({sector.top_score}) · {sector.high_confidence_count} high-confidence · {sector.stock_count} tracked
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-sm font-semibold tabular-nums text-text-primary">{sector.average_artha_score.toFixed(1)}</p>
                  <p className={`text-xs ${conditionTone(sector.trend)}`}>{prettyLabel(sector.trend)}</p>
                </div>
              </div>
            ))}
          </div>
        </article>
      </section>
    </div>
  );
}
