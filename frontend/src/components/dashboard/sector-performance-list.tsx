import type { SectorPerformance } from "@/lib/market-data";

function formatSignedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function SectorPerformanceList({ sectors }: { sectors: SectorPerformance[] }) {
  if (sectors.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
        Sector performance data is temporarily unavailable.
      </div>
    );
  }

  return (
    <div className="divide-y divide-border rounded-lg border border-border bg-card">
      {sectors.map((sector, index) => {
        const change = sector.market_cap_weighted_percent_change ? Number(sector.market_cap_weighted_percent_change) : null;
        return (
          <div key={sector.sector} className="flex items-center justify-between px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="text-xs text-text-secondary">{index + 1}</span>
              <span className="text-sm text-text-primary">{sector.sector}</span>
            </div>
            {change !== null ? (
              <span className={`text-sm font-medium ${change >= 0 ? "text-success-text" : "text-danger-text"}`}>
                {formatSignedPercent(change)}
              </span>
            ) : (
              <span className="text-xs text-text-secondary">No data yet</span>
            )}
          </div>
        );
      })}
    </div>
  );
}
