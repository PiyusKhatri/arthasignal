import type { StockIntelligence } from "@/lib/market-data";

const SCORE_PILLARS: {
  key: keyof StockIntelligence["scores"];
  label: string;
  weight: string;
  max: number;
  description: string;
  colorClass: string;
}[] = [
  { key: "trend", label: "Trend Structure", weight: "20%", max: 20, description: "SMA50/SMA200 structure and price vs long-term baseline", colorClass: "bg-emerald-500" },
  { key: "momentum", label: "Momentum", weight: "15%", max: 15, description: "RSI state and MACD confirmation", colorClass: "bg-blue-500" },
  { key: "liquidity", label: "Liquidity", weight: "10%", max: 10, description: "Execution quality based on the current liquidity tier", colorClass: "bg-cyan-500" },
  { key: "reliability", label: "Historical Evidence", weight: "30%", max: 30, description: "Market-wide signal edge vs baseline, sample depth, and confidence tier", colorClass: "bg-indigo-500" },
  { key: "valuation", label: "Fundamental Health", weight: "10%", max: 10, description: "Conservative EPS, P/E and P/B health contribution", colorClass: "bg-amber-500" },
  { key: "risk_adjustment", label: "Regime / Risk Control", weight: "15%", max: 15, description: "NEPSE regime, volatility, trend-break and overbought risk", colorClass: "bg-rose-500" },
];

export function IntelligenceBreakdown({ scores }: { scores: StockIntelligence["scores"] }) {
  return (
    <article className="rounded-xl border border-border bg-card p-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Pillar breakdown</p>
          <h3 className="mt-1 text-base font-semibold text-text-primary">Evidence-Weighted Artha Score</h3>
        </div>
        <span className="rounded-md border border-border bg-background px-2.5 py-1 text-xs font-semibold text-accent-text">
          100 points
        </span>
      </div>

      <div className="mt-5 space-y-3.5">
        {SCORE_PILLARS.map((pillar) => {
          const value = scores[pillar.key] ?? 0;
          const width = Math.max(0, Math.min(100, (value / pillar.max) * 100));
          return (
            <div key={pillar.key} className="rounded-lg border border-border/70 bg-background/50 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-text-primary">{pillar.label}</span>
                  <span className="rounded bg-background px-1.5 py-0.5 text-[10px] font-medium text-text-secondary">
                    {pillar.weight}
                  </span>
                </div>
                <p className="shrink-0 text-xs font-bold tabular-nums text-text-primary">
                  {value} <span className="font-normal text-text-secondary">/ {pillar.max}</span>
                </p>
              </div>
              <p className="mt-0.5 text-[11px] text-text-secondary">{pillar.description}</p>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background">
                <div className={`h-full rounded-full ${pillar.colorClass}`} style={{ width: `${width}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </article>
  );
}
