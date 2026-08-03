import { StatCard } from "@/components/dashboard/stat-card";
import type { MarketPulse, SectorPerformance } from "@/lib/market-data";

function formatSignedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function MarketPulseRow({ pulse, topSector }: { pulse: MarketPulse | null; topSector: SectorPerformance | null }) {
  const hasBreadth = (pulse?.advance_decline.total_symbols ?? 0) > 0;
  const turnoverRatio = pulse?.turnover_trend.turnover_vs_trailing_avg_ratio
    ? Number(pulse.turnover_trend.turnover_vs_trailing_avg_ratio)
    : null;
  const topSectorChange = topSector?.market_cap_weighted_percent_change
    ? Number(topSector.market_cap_weighted_percent_change)
    : null;

  const indexValue = pulse?.nepse_index?.current_value ?? null;
  const indexChange = pulse?.nepse_index?.percent_change ?? null;

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard
        label="NEPSE index"
        value={indexValue !== null ? indexValue.toLocaleString("en-US", { maximumFractionDigits: 2 }) : "N/A"}
        meta={
          indexChange !== null ? (
            <span className={indexChange >= 0 ? "text-success-text" : "text-danger-text"}>{formatSignedPercent(indexChange)}</span>
          ) : (
            "Index data unavailable"
          )
        }
      />

      <StatCard
        label="Advance / decline"
        value={hasBreadth ? `${pulse!.advance_decline.advances} / ${pulse!.advance_decline.declines}` : "No trades yet"}
        meta={hasBreadth ? pulse!.advance_decline.interpretation.replace(/_/g, " ") : "Check back once trading opens"}
      />

      <StatCard
        label="Turnover vs 20-day avg"
        value={turnoverRatio ? `${turnoverRatio.toFixed(2)}x` : "Pending"}
        meta={turnoverRatio ? "Relative to trailing 20-day average" : "Today's turnover isn't in yet"}
      />

      <StatCard
        label="Top sector"
        value={topSector?.sector ?? "N/A"}
        meta={
          topSectorChange !== null ? (
            <span className={topSectorChange >= 0 ? "text-success-text" : "text-danger-text"}>{formatSignedPercent(topSectorChange)}</span>
          ) : (
            "No sector data yet today"
          )
        }
      />
    </div>
  );
}
