import Link from "next/link";
import { MarketPulseRow } from "@/components/dashboard/market-pulse-row";
import { PaperTradeBanner } from "@/components/dashboard/paper-trade-banner";
import { SectorPerformanceList } from "@/components/dashboard/sector-performance-list";
import { TodaysSignalsRow } from "@/components/dashboard/todays-signals-row";
import { WatchlistTable } from "@/components/dashboard/watchlist-table";
import {
  WATCHLIST_SYMBOLS,
  getActiveSignals,
  getMarketIntelligence,
  getMarketPulseData,
  getSectorPerformance,
  getWatchlistData,
  type MarketIntelligenceStock,
} from "@/lib/market-data";

function prettyLabel(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

function scoreTone(score: number): string {
  if (score >= 70) return "bg-success/10 text-success-text";
  if (score >= 50) return "bg-warning/10 text-warning-text";
  return "bg-danger/10 text-danger-text";
}

function OpportunityCard({ stock, rank }: { stock: MarketIntelligenceStock; rank: number }) {
  return (
    <Link
      href={`/stock/${stock.symbol}`}
      className="group rounded-xl border border-border bg-background p-4 transition-colors hover:border-accent-primary-light"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold tabular-nums text-text-secondary">{String(rank).padStart(2, "0")}</span>
            <span className="text-base font-bold text-text-primary">{stock.symbol}</span>
          </div>
          <p className="mt-1 truncate text-xs text-text-secondary">{stock.company_name}</p>
        </div>
        <span className={`rounded-lg px-2.5 py-1 text-sm font-bold tabular-nums ${scoreTone(stock.artha_score)}`}>
          {stock.artha_score}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-1.5 text-[11px]">
        <span className="rounded-md border border-border px-2 py-1 text-text-secondary">{stock.sector ?? "Other"}</span>
        <span className="rounded-md border border-border px-2 py-1 text-text-secondary">{prettyLabel(stock.trend_strength)} trend</span>
        <span className="rounded-md border border-border px-2 py-1 text-text-secondary">{prettyLabel(stock.confidence_level)} confidence</span>
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs">
        <span className="text-text-secondary">{prettyLabel(stock.rating)}</span>
        <span className="font-medium text-accent-text transition-transform group-hover:translate-x-0.5">Analyze stock →</span>
      </div>
    </Link>
  );
}

export default async function DashboardPage() {
  const [pulse, activeSignals, sectors, watchlist, intelligence] = await Promise.all([
    getMarketPulseData(),
    getActiveSignals(),
    getSectorPerformance(),
    getWatchlistData(WATCHLIST_SYMBOLS),
    getMarketIntelligence(300),
  ]);

  const rankedSectors = [...(sectors ?? [])].sort((a, b) => {
    const aValue = a.market_cap_weighted_percent_change === null ? Number.NEGATIVE_INFINITY : Number(a.market_cap_weighted_percent_change);
    const bValue = b.market_cap_weighted_percent_change === null ? Number.NEGATIVE_INFINITY : Number(b.market_cap_weighted_percent_change);
    return bValue - aValue;
  });
  const topSector = rankedSectors.find((sector) => sector.market_cap_weighted_percent_change !== null) ?? null;
  const topOpportunities = intelligence?.top_opportunities.slice(0, 6) ?? [];
  const market = intelligence?.market ?? null;
  const dataDate = intelligence?.as_of_date ?? pulse?.date ?? activeSignals?.as_of_date ?? null;

  const unavailableSources = [
    pulse === null ? "market pulse" : null,
    activeSignals === null ? "signals" : null,
    sectors === null ? "sector performance" : null,
    intelligence === null ? "market intelligence" : null,
    watchlist.length === 0 && WATCHLIST_SYMBOLS.length > 0 ? "watchlist quotes" : null,
  ].filter((value): value is string => value !== null);

  return (
    <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-6">
      <PaperTradeBanner />

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="grid gap-6 p-5 sm:p-6 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)] xl:p-7">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent-text">Market command center</p>
            <h1 className="mt-2 max-w-3xl text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">
              See the market state, strongest setups and what deserves your attention next.
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-text-secondary">
              ArthaSignal combines NEPSE breadth, technical structure, liquidity and validated signal evidence into one decision-focused dashboard.
            </p>

            <div className="mt-5 flex flex-wrap items-center gap-2">
              <Link href="/market-pulse" className="rounded-lg bg-accent-primary px-3.5 py-2 text-sm font-semibold text-white transition-opacity hover:opacity-90">
                Open Market Intelligence
              </Link>
              <Link href="/sectors" className="rounded-lg border border-border px-3.5 py-2 text-sm font-medium text-text-primary hover:bg-background">
                Explore sectors
              </Link>
              <span className="ml-1 text-xs text-text-secondary">
                Press <kbd className="rounded border border-border bg-background px-1.5 py-0.5 font-mono text-[10px]">⌘K</kbd> to find any stock.
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 rounded-xl border border-border bg-background p-3">
            <div className="rounded-lg bg-card p-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-text-secondary">Condition</p>
              <p className="mt-2 text-base font-semibold text-text-primary">{prettyLabel(market?.condition)}</p>
            </div>
            <div className="rounded-lg bg-card p-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-text-secondary">Risk</p>
              <p className="mt-2 text-base font-semibold text-text-primary">{prettyLabel(market?.risk_level)}</p>
            </div>
            <div className="rounded-lg bg-card p-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-text-secondary">Avg Artha</p>
              <p className="mt-2 text-xl font-semibold tabular-nums text-text-primary">
                {market ? market.average_artha_score.toFixed(1) : "—"}
              </p>
            </div>
            <div className="rounded-lg bg-card p-3">
              <p className="text-[11px] uppercase tracking-[0.12em] text-text-secondary">Positive setups</p>
              <p className="mt-2 text-xl font-semibold tabular-nums text-text-primary">{percent(market?.positive_setup_share)}</p>
            </div>
            <div className="col-span-2 flex items-center justify-between rounded-lg border border-border px-3 py-2 text-xs text-text-secondary">
              <span>{market ? `${market.stocks_analyzed} stocks analyzed` : "Market intelligence unavailable"}</span>
              <span>{dataDate ? `As of ${dataDate}` : "Latest available session"}</span>
            </div>
          </div>
        </div>
      </section>

      {unavailableSources.length > 0 ? (
        <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-text-secondary">
          Some market data is temporarily unavailable: {unavailableSources.join(", ")}. Empty placeholders below should not be interpreted as a market signal.
        </div>
      ) : null}

      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Live context</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">Market pulse</h2>
          </div>
          <Link href="/market-pulse" className="text-xs font-medium text-accent-text hover:underline">View full intelligence</Link>
        </div>
        <MarketPulseRow pulse={pulse} topSector={topSector} />
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,0.55fr)]">
        <article className="rounded-2xl border border-border bg-card p-4 sm:p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Artha ranking</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Top opportunities</h2>
              <p className="mt-1 text-xs text-text-secondary">Highest-scoring setups from the current market intelligence snapshot.</p>
            </div>
            <Link href="/market-pulse" className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text-primary hover:bg-background">
              Open screener
            </Link>
          </div>

          {topOpportunities.length > 0 ? (
            <div className="mt-4 grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
              {topOpportunities.map((stock, index) => <OpportunityCard key={stock.symbol} stock={stock} rank={index + 1} />)}
            </div>
          ) : (
            <div className="mt-4 rounded-xl border border-border bg-background p-5 text-sm text-text-secondary">
              {intelligence === null
                ? "Market intelligence is temporarily unavailable."
                : "No stock currently clears the evidence, confidence and active-signal gates for a qualified setup."}
            </div>
          )}
        </article>

        <article>
          <div className="mb-3 flex items-end justify-between gap-2">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Leadership</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Sector momentum</h2>
            </div>
            <Link href="/sectors" className="text-xs font-medium text-accent-text hover:underline">View all</Link>
          </div>
          <SectorPerformanceList sectors={rankedSectors.slice(0, 6)} />
        </article>
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Actionable setups</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">Today&apos;s signals</h2>
          </div>
          {activeSignals?.as_of_date ? <p className="text-xs text-text-secondary">As of {activeSignals.as_of_date}</p> : null}
        </div>
        <TodaysSignalsRow
          signals={activeSignals?.signals ?? []}
          unavailable={activeSignals === null}
          asOfDate={null}
        />
      </section>

      <section className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(320px,0.45fr)]">
        <div>
          <div className="mb-3 flex items-end justify-between gap-2">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Your focus list</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Watchlist</h2>
            </div>
            <Link href="/watchlist" className="text-xs font-medium text-accent-text hover:underline">Manage watchlist</Link>
          </div>
          <WatchlistTable stocks={watchlist} />
        </div>

        <aside className="rounded-2xl border border-border bg-card p-5">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-secondary">Workflow</p>
          <h2 className="mt-1 text-lg font-semibold text-text-primary">Research faster</h2>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            Use the global stock search to jump straight into Artha Score, technical health, signal history and the full TradingView-style workspace.
          </p>
          <div className="mt-5 space-y-2">
            <Link href="/market-pulse" className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text-primary hover:border-accent-primary-light">
              Market screener <span className="text-text-secondary">→</span>
            </Link>
            <Link href="/sectors" className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text-primary hover:border-accent-primary-light">
              Sector analysis <span className="text-text-secondary">→</span>
            </Link>
            <Link href="/watchlist" className="flex items-center justify-between rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-text-primary hover:border-accent-primary-light">
              Watchlist <span className="text-text-secondary">→</span>
            </Link>
          </div>
        </aside>
      </section>
    </div>
  );
}
