import type { StockIntelligence } from "@/lib/market-data";

function prettyLabel(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function scoreTone(score: number): string {
  if (score >= 70) return "text-success-text";
  if (score >= 50) return "text-warning-text";
  return "text-danger-text";
}

function riskTone(level: StockIntelligence["risk"]["level"]): string {
  if (level === "low") return "text-success-text";
  if (level === "medium") return "text-warning-text";
  return "text-danger-text";
}

export function ArthaScoreCard({ intelligence }: { intelligence: StockIntelligence }) {
  return (
    <article className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Artha Score</p>
          <div className="mt-2 flex items-end gap-2">
            <span className={`text-5xl font-semibold tabular-nums ${scoreTone(intelligence.artha_score)}`}>
              {intelligence.artha_score}
            </span>
            <span className="pb-1 text-sm text-text-secondary">/ 100</span>
          </div>
          <p className="mt-2 max-w-sm text-xs leading-relaxed text-text-secondary">
            Evidence-weighted setup quality. This score is not a probability of profit.
          </p>
        </div>
        <div className="rounded-lg border border-border bg-background px-3 py-2 text-right">
          <p className="text-xs text-text-secondary">Rating</p>
          <p className="mt-0.5 text-sm font-semibold text-text-primary">{prettyLabel(intelligence.rating)}</p>
        </div>
      </div>

      <div className="mt-5 h-2 overflow-hidden rounded-full bg-background">
        <div
          className="h-full rounded-full bg-accent-primary-light transition-[width]"
          style={{ width: `${Math.max(0, Math.min(100, intelligence.artha_score))}%` }}
        />
      </div>

      <dl className="mt-5 grid grid-cols-3 gap-3 border-t border-border pt-4 text-sm">
        <div>
          <dt className="text-xs text-text-secondary">Evidence confidence</dt>
          <dd className="mt-1 font-medium text-text-primary">{prettyLabel(intelligence.confidence_level)}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-secondary">Trend</dt>
          <dd className="mt-1 font-medium text-text-primary">{prettyLabel(intelligence.trend_strength)}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-secondary">Risk</dt>
          <dd className={`mt-1 font-medium ${riskTone(intelligence.risk.level)}`}>{prettyLabel(intelligence.risk.level)}</dd>
        </div>
      </dl>
    </article>
  );
}
