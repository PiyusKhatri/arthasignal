import Link from "next/link";
import { ArrowRight, Layers3, TrendingDown, TrendingUp } from "lucide-react";
import { SectorRankTable } from "@/components/sectors/sector-rank-table";
import { getSectorPerformance } from "@/lib/market-data";

function numberOrNull(value: string | null): number | null {
  return value === null ? null : Number(value);
}

function formatSigned(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export default async function SectorsPage() {
  const sectors = (await getSectorPerformance()) ?? [];
  const ranked = [...sectors].sort((a, b) => {
    const aValue = numberOrNull(a.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY;
    const bValue = numberOrNull(b.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY;
    return bValue - aValue;
  });

  const leader = ranked.find((sector) => sector.market_cap_weighted_percent_change !== null) ?? null;
  const laggard = [...ranked].reverse().find((sector) => sector.market_cap_weighted_percent_change !== null) ?? null;
  const advancingSectors = sectors.filter((sector) => (numberOrNull(sector.market_cap_weighted_percent_change) ?? 0) > 0).length;
  const advances = sectors.reduce((sum, sector) => sum + sector.advance_decline.advances, 0);
  const declines = sectors.reduce((sum, sector) => sum + sector.advance_decline.declines, 0);
  const totalBreadth = advances + declines;
  const marketBreadth = totalBreadth > 0 ? advances / totalBreadth : null;

  return (
    <div className="flex flex-col gap-7">
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="grid gap-6 px-5 py-6 sm:px-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.6fr)] lg:items-end">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-accent-text">
              <Layers3 className="size-4" aria-hidden="true" /> Market structure
            </div>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">Sector intelligence</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
              See where NEPSE strength and weakness are concentrated, compare breadth and turnover, then drill directly into the stocks driving each sector.
            </p>
          </div>

          <div className="flex flex-wrap gap-2 lg:justify-end">
            <Link
              href="/market-pulse"
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-text-primary transition-colors hover:border-accent-primary-light"
            >
              Open market intelligence <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </div>
        </div>

        <div className="grid border-t border-border sm:grid-cols-2 xl:grid-cols-4">
          <div className="border-b border-border p-5 sm:border-r xl:border-b-0">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">Leading sector</p>
            <div className="mt-2 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-base font-semibold text-text-primary">{leader?.sector ?? "Unavailable"}</p>
                <p className="mt-1 text-xs text-text-secondary">Strongest weighted move</p>
              </div>
              <span className="inline-flex items-center gap-1 rounded-lg bg-success/10 px-2.5 py-1 text-sm font-semibold text-success-text">
                <TrendingUp className="size-4" aria-hidden="true" />
                {formatSigned(numberOrNull(leader?.market_cap_weighted_percent_change ?? null))}
              </span>
            </div>
          </div>

          <div className="border-b border-border p-5 xl:border-b-0 xl:border-r">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">Weakest sector</p>
            <div className="mt-2 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-base font-semibold text-text-primary">{laggard?.sector ?? "Unavailable"}</p>
                <p className="mt-1 text-xs text-text-secondary">Largest weighted decline</p>
              </div>
              <span className="inline-flex items-center gap-1 rounded-lg bg-danger/10 px-2.5 py-1 text-sm font-semibold text-danger-text">
                <TrendingDown className="size-4" aria-hidden="true" />
                {formatSigned(numberOrNull(laggard?.market_cap_weighted_percent_change ?? null))}
              </span>
            </div>
          </div>

          <div className="border-b border-border p-5 sm:border-r sm:border-b-0">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">Sector participation</p>
            <p className="mt-2 text-2xl font-semibold text-text-primary">{advancingSectors}<span className="text-sm font-medium text-text-secondary"> / {sectors.length || 0}</span></p>
            <p className="mt-1 text-xs text-text-secondary">Sectors trading positive</p>
          </div>

          <div className="p-5">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-text-secondary">Market breadth</p>
            <p className="mt-2 text-2xl font-semibold text-text-primary">{marketBreadth === null ? "—" : `${Math.round(marketBreadth * 100)}%`}</p>
            <p className="mt-1 text-xs text-text-secondary">{advances} advancing · {declines} declining stocks</p>
          </div>
        </div>
      </section>

      <SectorRankTable sectors={ranked} />
    </div>
  );
}
