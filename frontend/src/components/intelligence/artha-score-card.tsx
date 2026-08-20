import type { StockIntelligence } from "@/lib/market-data";

type IntelligenceWithEvidenceScore = StockIntelligence & { confidence_score?: number };

function scoreTone(score: number): string {
  if (score >= 70) return "text-success-text";
  if (score >= 50) return "text-warning-text";
  return "text-danger-text";
}

export function ArthaScoreCard({ intelligence }: { intelligence: IntelligenceWithEvidenceScore }) {
  return (
    <article className="rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Artha Score</p>
          <div className="mt-2 flex items-end gap-2">
            <span className={`text-5xl font-semibold tabular-nums ${scoreTone(intelligence.artha_score)}`}>
              {intelligence.artha_score}
            </span>
            <span className="pb-1 text-sm text-text-secondary">/ 100</span>
          </div>
          <p className="mt-2 max-w-lg text-sm leading-relaxed text-text-secondary">
            Overall setup quality from trend, momentum, historical evidence, liquidity, market risk, and fundamentals.
          </p>
        </div>

        <div className="min-w-[210px] rounded-lg border border-border bg-background p-4">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="text-text-secondary">Setup quality</span>
            <span className="font-semibold text-text-primary">{intelligence.artha_score}/100</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-card">
            <div
              className="h-full rounded-full bg-accent-primary-light transition-[width]"
              style={{ width: `${Math.max(0, Math.min(100, intelligence.artha_score))}%` }}
            />
          </div>
          <p className="mt-3 text-xs leading-relaxed text-text-secondary">
            This is a setup score, not a probability of profit. Confidence is shown separately below.
          </p>
        </div>
      </div>
    </article>
  );
}
