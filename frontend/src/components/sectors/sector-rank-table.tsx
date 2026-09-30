"use client";

import Link from "next/link";
import { ArrowUpRight, ChevronDown, Search, SlidersHorizontal, TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import type { SectorPerformance } from "@/lib/market-data";

type FilterKey = "all" | "gainers" | "decliners";
type SortKey = "performance" | "breadth" | "turnover";

function asNumber(value: string | null): number | null {
  return value === null ? null : Number(value);
}

function formatSignedPercent(value: string | null): string {
  const num = asNumber(value);
  if (num === null || !Number.isFinite(num)) return "No data";
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function formatTurnoverRatio(value: string | null): string {
  const num = asNumber(value);
  if (num === null || !Number.isFinite(num)) return "—";
  return `${num.toFixed(2)}x`;
}

function breadthScore(sector: SectorPerformance): number {
  const { advances, declines } = sector.advance_decline;
  const total = advances + declines;
  return total > 0 ? advances / total : 0;
}

function brokerConcentrationSummary(sector: SectorPerformance): { label: string; reliable: boolean } {
  const bc = sector.broker_concentration;
  if (!bc.available) return { label: "Floorsheet unavailable", reliable: false };
  if (!bc.coverage_reliable) return { label: "Broker coverage is thin", reliable: false };
  const combined = bc.top_brokers.reduce((sum, broker) => sum + Number(broker.fraction_of_sector_turnover ?? 0), 0);
  return { label: `Top ${bc.top_brokers.length}: ${(combined * 100).toFixed(0)}% turnover`, reliable: true };
}

function PerformanceTrack({ value, maxAbs }: { value: number; maxAbs: number }) {
  const width = maxAbs > 0 ? Math.min(100, (Math.abs(value) / maxAbs) * 100) : 0;
  const up = value >= 0;
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-background">
      <div className={`h-full rounded-full ${up ? "bg-success-text" : "bg-danger-text"}`} style={{ width: `${width}%` }} />
    </div>
  );
}

export function SectorRankTable({ sectors }: { sectors: SectorPerformance[] }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [sortBy, setSortBy] = useState<SortKey>("performance");

  const originalRank = useMemo(() => new Map(sectors.map((sector, index) => [sector.sector, index + 1])), [sectors]);
  const maxAbsChange = useMemo(
    () => Math.max(...sectors.map((sector) => Math.abs(asNumber(sector.market_cap_weighted_percent_change) ?? 0)), 0.01),
    [sectors],
  );

  const visibleSectors = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return sectors
      .filter((sector) => !normalized || sector.sector.toLowerCase().includes(normalized))
      .filter((sector) => {
        const change = asNumber(sector.market_cap_weighted_percent_change);
        if (filter === "gainers") return change !== null && change > 0;
        if (filter === "decliners") return change !== null && change < 0;
        return true;
      })
      .sort((a, b) => {
        if (sortBy === "breadth") return breadthScore(b) - breadthScore(a);
        if (sortBy === "turnover") {
          return (asNumber(b.turnover_trend.turnover_vs_trailing_avg_ratio) ?? -1) - (asNumber(a.turnover_trend.turnover_vs_trailing_avg_ratio) ?? -1);
        }
        return (asNumber(b.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY) - (asNumber(a.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY);
      });
  }, [filter, query, sectors, sortBy]);

  if (sectors.length === 0) {
    return (
      <div className="rounded-2xl border border-border bg-card p-8 text-center">
        <p className="text-sm font-medium text-text-primary">Sector data is temporarily unavailable</p>
        <p className="mt-1 text-sm text-text-secondary">This is a data-service state, not a market signal.</p>
      </div>
    );
  }

  return (
    <section>
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-secondary">Sector board</p>
          <h2 className="mt-1 text-xl font-semibold tracking-tight text-text-primary">Compare market leadership</h2>
          <p className="mt-1 text-sm text-text-secondary">Filter and rank sectors here, then open a dedicated sector page for deeper analysis.</p>
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative min-w-0 sm:w-56">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-text-secondary" aria-hidden="true" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Find sector..."
              aria-label="Find sector"
              className="w-full rounded-lg border border-border bg-card py-2 pl-9 pr-3 text-sm text-text-primary outline-none transition-colors placeholder:text-text-secondary focus:border-accent-primary-light"
            />
          </div>

          <div className="inline-flex rounded-lg border border-border bg-card p-1">
            {(["all", "gainers", "decliners"] as FilterKey[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setFilter(item)}
                aria-pressed={filter === item}
                className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize transition-colors ${
                  filter === item ? "bg-accent-primary text-white" : "text-text-secondary hover:bg-background hover:text-text-primary"
                }`}
              >
                {item}
              </button>
            ))}
          </div>

          <label className="relative flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs text-text-secondary">
            <SlidersHorizontal className="size-3.5" aria-hidden="true" />
            <span className="sr-only">Sort sectors</span>
            <select
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value as SortKey)}
              className="appearance-none bg-transparent pr-4 font-medium text-text-primary outline-none"
            >
              <option value="performance">Performance</option>
              <option value="breadth">Breadth</option>
              <option value="turnover">Turnover</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 size-3.5" aria-hidden="true" />
          </label>
        </div>
      </div>

      {visibleSectors.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-card px-6 py-12 text-center text-sm text-text-secondary">
          No sectors match the current filters.
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {visibleSectors.map((sector) => {
            const change = asNumber(sector.market_cap_weighted_percent_change);
            const up = change !== null && change >= 0;
            const { advances, declines, unchanged, total_symbols: totalSymbols } = sector.advance_decline;
            const breadth = breadthScore(sector);
            const broker = brokerConcentrationSummary(sector);
            const indexChange = sector.sector_index.percent_change;
            const turnoverRatio = sector.turnover_trend.turnover_vs_trailing_avg_ratio;
            const href = `/sectors/${encodeURIComponent(sector.sector)}`;

            return (
              <Link
                key={sector.sector}
                href={href}
                className="group overflow-hidden rounded-2xl border border-border bg-card transition-all hover:-translate-y-0.5 hover:border-accent-primary-light/70 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-primary"
              >
                <article className="p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="flex size-7 items-center justify-center rounded-lg bg-background text-[11px] font-semibold tabular-nums text-text-secondary">
                          {String(originalRank.get(sector.sector) ?? 0).padStart(2, "0")}
                        </span>
                        <div className="min-w-0">
                          <h3 className="truncate text-base font-semibold text-text-primary">{sector.sector}</h3>
                          <p className="mt-0.5 truncate text-xs text-text-secondary">{sector.sector_index.index_name || "Sector market data"}</p>
                        </div>
                      </div>
                    </div>

                    <span
                      className={`inline-flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1 text-sm font-semibold ${
                        change === null ? "bg-background text-text-secondary" : up ? "bg-success/10 text-success-text" : "bg-danger/10 text-danger-text"
                      }`}
                    >
                      {change !== null ? up ? <TrendingUp className="size-4" aria-hidden="true" /> : <TrendingDown className="size-4" aria-hidden="true" /> : null}
                      {formatSignedPercent(sector.market_cap_weighted_percent_change)}
                    </span>
                  </div>

                  {change !== null ? <div className="mt-4"><PerformanceTrack value={change} maxAbs={maxAbsChange} /></div> : null}

                  <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-xl bg-background p-3">
                      <p className="text-[11px] font-medium uppercase tracking-[0.1em] text-text-secondary">Breadth</p>
                      <div className="mt-2 flex items-baseline justify-between gap-2">
                        <span className="font-semibold text-text-primary">{Math.round(breadth * 100)}%</span>
                        <span className="text-xs text-text-secondary">{advances}↑ {declines}↓ {unchanged}·</span>
                      </div>
                      <div className="mt-2 h-1 overflow-hidden rounded-full bg-border">
                        <div className="h-full rounded-full bg-success-text" style={{ width: `${Math.min(100, breadth * 100)}%` }} />
                      </div>
                    </div>

                    <div className="rounded-xl bg-background p-3">
                      <p className="text-[11px] font-medium uppercase tracking-[0.1em] text-text-secondary">Turnover</p>
                      <p className="mt-2 font-semibold text-text-primary">{formatTurnoverRatio(turnoverRatio)}</p>
                      <p className="mt-1 text-xs text-text-secondary">vs 20-day average</p>
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4 text-xs text-text-secondary">
                    <span>{totalSymbols} tracked stocks</span>
                    <span>{indexChange !== null ? `${formatSignedPercent(indexChange)} sector index` : "Sector index unavailable"}</span>
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-3 text-xs">
                    <span className={broker.reliable ? "text-text-primary" : "text-text-secondary"}>{broker.label}</span>
                    <span className="inline-flex shrink-0 items-center gap-1 font-semibold text-accent-text">
                      Open sector <ArrowUpRight className="size-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" aria-hidden="true" />
                    </span>
                  </div>
                </article>
              </Link>
            );
          })}
        </div>
      )}
    </section>
  );
}
