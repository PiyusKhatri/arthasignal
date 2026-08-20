import type { StockIntelligence } from "@/lib/market-data";

export function AiAnalystCard({ intelligence }: { intelligence: StockIntelligence }) {
  const analysis = intelligence.ai_analysis;
  if (!analysis) return null;

  return (
    <article className="relative overflow-hidden rounded-xl border border-accent-primary/40 bg-gradient-to-br from-card via-card to-accent-primary/10 p-5 shadow-lg shadow-black/5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 pb-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent-primary/20 text-accent-text">
            <svg className="h-4 w-4 fill-current" viewBox="0 0 24 24">
              <path d="M12 2L14.4 8.6L21 11L14.4 13.4L12 20L9.6 13.4L3 11L9.6 8.6L12 2Z" />
            </svg>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-text-primary">Artha AI Analyst</h3>
              <span className="rounded-full bg-accent-primary/20 px-2 py-0.5 text-[10px] font-semibold text-accent-text uppercase tracking-wider">
                {analysis.provider === "mistral_ai" ? "Mistral AI" : "Deterministic Engine"}
              </span>
            </div>
            <p className="text-[11px] text-text-secondary">Explanation of the quantitative evidence for {intelligence.symbol}</p>
          </div>
        </div>
        <div className="text-right text-[11px] text-text-secondary">
          <span>Evidence horizon: </span>
          <strong className="text-text-primary font-medium">Multi-week</strong>
        </div>
      </div>

      <div className="mt-4">
        <p className="text-sm leading-relaxed text-text-primary font-normal">
          &ldquo;{analysis.summary}&rdquo;
        </p>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-border/70 bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-wider text-text-secondary font-semibold">Evidence Takeaway</p>
          <p className="mt-1 text-xs font-medium text-text-primary">{analysis.key_takeaway}</p>
        </div>
        <div className="rounded-lg border border-border/70 bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-wider text-text-secondary font-semibold">Confidence Basis</p>
          <p className="mt-1 text-xs text-text-secondary">{analysis.confidence_reason}</p>
        </div>
      </div>
    </article>
  );
}
