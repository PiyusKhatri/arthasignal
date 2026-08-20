import type { StockIntelligence } from "@/lib/market-data";

const SCORE_ROWS: {
  key: keyof StockIntelligence["scores"];
  label: string;
  max: number;
  description: string;
}[] = [
  { key: "trend", label: "Trend", max: 30, description: "Long-term moving-average structure" },
  { key: "momentum", label: "Momentum", max: 20, description: "RSI and MACD confirmation" },
  { key: "liquidity", label: "Liquidity", max: 15, description: "Execution quality and turnover tier" },
  { key: "reliability", label: "Reliability", max: 20, description: "Historical confidence of active signals" },
];

export function IntelligenceBreakdown({ scores }: { scores: StockIntelligence["scores"] }) {
  return (
    <article className="rounded-xl border border-border bg-card p-5">
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Score breakdown</p>
        <h3 className="mt-1 text-base font-semibold text-text-primary">What is driving the score</h3>
      </div>

      <div className="mt-5 space-y-4">
        {SCORE_ROWS.map((row) => {
          const value = scores[row.key];
          const width = Math.max(0, Math.min(100, (value / row.max) * 100));
          return (
            <div key={row.key}>
              <div className="flex items-end justify-between gap-4">
                <div>
                  <p className="text-sm font-medium text-text-primary">{row.label}</p>
                  <p className="text-xs text-text-secondary">{row.description}</p>
                </div>
                <p className="shrink-0 text-sm font-semibold tabular-nums text-text-primary">
                  {value} <span className="font-normal text-text-secondary">/ {row.max}</span>
                </p>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background">
                <div className="h-full rounded-full bg-accent-primary-light" style={{ width: `${width}%` }} />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-5 flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5">
        <div>
          <p className="text-sm font-medium text-text-primary">Risk adjustment</p>
          <p className="text-xs text-text-secondary">Penalty applied for identified technical risks</p>
        </div>
        <span className={scores.risk_adjustment < 0 ? "font-semibold text-danger-text" : "font-semibold text-success-text"}>
          {scores.risk_adjustment > 0 ? "+" : ""}{scores.risk_adjustment}
        </span>
      </div>
    </article>
  );
}
