import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  BarChart3,
  Building2,
  CircleDollarSign,
  Gauge,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  Users,
} from "lucide-react";
import { SectorStocksPanel } from "@/components/sectors/sector-stocks-panel";
import { getSectorPerformance, type SectorPerformance } from "@/lib/market-data";

function asNumber(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatSignedPercent(value: string | null | undefined): string {
  const num = asNumber(value);
  if (num === null) return "—";
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function formatTurnoverRatio(value: string | null): string {
  const num = asNumber(value);
  return num === null ? "—" : `${num.toFixed(2)}x`;
}

function formatMoney(value: string | null): string {
  const num = asNumber(value);
  if (num === null) return "—";
  return `Rs ${new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(num)}`;
}

function rankSectors(sectors: SectorPerformance[]): SectorPerformance[] {
  return [...sectors].sort(
    (a, b) =>
      (asNumber(b.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY) -
      (asNumber(a.market_cap_weighted_percent_change) ?? Number.NEGATIVE_INFINITY),
  );
}

function decodeSegment(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function brokerShare(sector: SectorPerformance): number | null {
  const brokers = sector.broker_concentration;
  if (!brokers.available || !brokers.coverage_reliable || brokers.top_brokers.length === 0) return null;
  return brokers.top_brokers.reduce((sum, broker) => sum + Number(broker.fraction_of_sector_turnover ?? 0), 0);
}

export default async function SectorDetailPage({ params }: { params: Promise<{ sector: string }> }) {
  const { sector: encodedSector } = await params;
  const requestedSector = decodeSegment(encodedSector).trim();
  const sectors = await getSectorPerformance();

  if (sectors === null) {
    return (
      <div className="mx-auto max-w-4xl">
        <Link href="/sectors" className="inline-flex items-center gap-2 text-sm font-medium text-accent-text hover:underline">
          <ArrowLeft className="size-4" aria-hidden="true" /> Back to sectors
        </Link>
        <section className="mt-6 rounded-2xl border border-warning/30 bg-warning/10 p-6">
          <h1 className="text-xl font-semibold text-text-primary">Sector data is temporarily unavailable</h1>
          <p className="mt-2 text-sm leading-6 text-text-secondary">
            ArthaSignal could not load the latest sector snapshot. This is a data-service state, not a market signal.
          </p>
        </section>
      </div>
    );
  }

  const sector = sectors.find((item) => item.sector.toLowerCase() === requestedSector.toLowerCase());
  if (!sector) notFound();

  const ranked = rankSectors(sectors);
  const rank = ranked.findIndex((item) => item.sector === sector.sector) + 1;
  const change = asNumber(sector.market_cap_weighted_percent_change);
  const isPositive = change !== null && change >= 0;
  const { advances, declines, unchanged, total_symbols: trackedStocks } = sector.advance_decline;
  const breadthTotal = advances + declines + unchanged;
  const advanceShare = breadthTotal > 0 ? advances / breadthTotal : 0;
  const declineShare = breadthTotal > 0 ? declines / breadthTotal : 0;
  const unchangedShare = breadthTotal > 0 ? unchanged / breadthTotal : 0;
  const turnoverRatio = asNumber(sector.turnover_trend.turnover_vs_trailing_avg_ratio);
  const topBrokerShare = brokerShare(sector);
  const concentration = sector.broker_concentration;

  return (
    <div className="flex flex-col gap-7">
      <div>
        <Link href="/sectors" className="inline-flex items-center gap-2 text-sm font-medium text-accent-text hover:underline">
          <ArrowLeft className="size-4" aria-hidden="true" /> Back to sector board
        </Link>
      </div>

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="grid gap-6 px-5 py-6 sm:px-6 lg:grid-cols-[minmax(0,1.35fr)_minmax(300px,0.65fr)] lg:items-end">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-border bg-background px-2.5 py-1 text-xs font-semibold text-text-secondary">
                Sector rank #{rank || "—"} of {sectors.length}
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold ${
                  change === null ? "bg-background text-text-secondary" : isPositive ? "bg-success/10 text-success-text" : "bg-danger/10 text-danger-text"
                }`}
              >
                {change !== null ? isPositive ? <TrendingUp className="size-3.5" aria-hidden="true" /> : <TrendingDown className="size-3.5" aria-hidden="true" /> : null}
                {formatSignedPercent(sector.market_cap_weighted_percent_change)} today
              </span>
            </div>

            <h1 className="mt-4 text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">{sector.sector}</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-text-secondary">
              Deep sector view combining weighted performance, market breadth, turnover participation, broker concentration and the stocks currently driving this group.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-background p-4">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-text-secondary">Tracked sector index</p>
            <p className="mt-2 text-base font-semibold text-text-primary">{sector.sector_index.index_name || "Sector index unavailable"}</p>
            <div className="mt-3 flex items-end justify-between gap-3">
              <div>
                <p className="text-xs text-text-secondary">Current value</p>
                <p className="mt-1 text-lg font-semibold tabular-nums text-text-primary">
                  {sector.sector_index.current_value === null
                    ? "—"
                    : Number(sector.sector_index.current_value).toLocaleString("en-US", { maximumFractionDigits: 2 })}
                </p>
              </div>
              <span className={`text-sm font-semibold ${asNumber(sector.sector_index.percent_change) === null ? "text-text-secondary" : (asNumber(sector.sector_index.percent_change) ?? 0) >= 0 ? "text-success-text" : "text-danger-text"}`}>
                {formatSignedPercent(sector.sector_index.percent_change)}
              </span>
            </div>
          </div>
        </div>

        <div className="grid border-t border-border sm:grid-cols-2 xl:grid-cols-4">
          <div className="border-b border-border p-5 sm:border-r xl:border-b-0">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-text-secondary">
              <BarChart3 className="size-4" aria-hidden="true" /> Weighted move
            </div>
            <p className={`mt-3 text-2xl font-semibold tabular-nums ${change === null ? "text-text-primary" : isPositive ? "text-success-text" : "text-danger-text"}`}>
              {formatSignedPercent(sector.market_cap_weighted_percent_change)}
            </p>
            <p className="mt-1 text-xs text-text-secondary">Market-cap-weighted sector change</p>
          </div>

          <div className="border-b border-border p-5 xl:border-b-0 xl:border-r">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-text-secondary">
              <Users className="size-4" aria-hidden="true" /> Breadth
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums text-text-primary">{breadthTotal > 0 ? `${Math.round(advanceShare * 100)}%` : "—"}</p>
            <p className="mt-1 text-xs text-text-secondary">{advances} advancing · {declines} declining · {unchanged} flat</p>
          </div>

          <div className="border-b border-border p-5 sm:border-r sm:border-b-0">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-text-secondary">
              <CircleDollarSign className="size-4" aria-hidden="true" /> Turnover
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums text-text-primary">{formatTurnoverRatio(sector.turnover_trend.turnover_vs_trailing_avg_ratio)}</p>
            <p className="mt-1 text-xs text-text-secondary">vs trailing 20-day average</p>
          </div>

          <div className="p-5">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.1em] text-text-secondary">
              <Building2 className="size-4" aria-hidden="true" /> Constituents
            </div>
            <p className="mt-3 text-2xl font-semibold tabular-nums text-text-primary">{trackedStocks}</p>
            <p className="mt-1 text-xs text-text-secondary">Stocks represented in breadth data</p>
          </div>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.55fr)]">
        <article className="rounded-2xl border border-border bg-card p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-secondary">Market structure</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Participation inside {sector.sector}</h2>
            </div>
            <Gauge className="size-5 text-accent-text" aria-hidden="true" />
          </div>

          <div className="mt-6">
            <div className="flex items-center justify-between text-xs text-text-secondary">
              <span>Advance / decline mix</span>
              <span>{breadthTotal} symbols with session status</span>
            </div>
            <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-background" aria-label="Sector breadth distribution">
              <div className="bg-success-text" style={{ width: `${advanceShare * 100}%` }} title={`${advances} advancing`} />
              <div className="bg-danger-text" style={{ width: `${declineShare * 100}%` }} title={`${declines} declining`} />
              <div className="bg-border" style={{ width: `${unchangedShare * 100}%` }} title={`${unchanged} unchanged`} />
            </div>
            <div className="mt-3 flex flex-wrap gap-4 text-xs">
              <span className="font-medium text-success-text">{advances} advancing</span>
              <span className="font-medium text-danger-text">{declines} declining</span>
              <span className="font-medium text-text-secondary">{unchanged} unchanged</span>
            </div>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-xl bg-background p-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-text-secondary">Today turnover</p>
              <p className="mt-2 text-base font-semibold text-text-primary">{formatMoney(sector.turnover_trend.today_turnover)}</p>
            </div>
            <div className="rounded-xl bg-background p-4">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-text-secondary">20-day average</p>
              <p className="mt-2 text-base font-semibold text-text-primary">{formatMoney(sector.turnover_trend.trailing_20_day_avg_turnover)}</p>
            </div>
            <div className="rounded-xl bg-background p-4 sm:col-span-2 lg:col-span-1">
              <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-text-secondary">Relative participation</p>
              <p className="mt-2 text-base font-semibold text-text-primary">{turnoverRatio === null ? "Unavailable" : turnoverRatio >= 1 ? "Above normal" : "Below normal"}</p>
              <p className="mt-1 text-xs text-text-secondary">{formatTurnoverRatio(sector.turnover_trend.turnover_vs_trailing_avg_ratio)} normal turnover</p>
            </div>
          </div>
        </article>

        <article className="rounded-2xl border border-border bg-card p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-secondary">Floorsheet context</p>
              <h2 className="mt-1 text-lg font-semibold text-text-primary">Broker concentration</h2>
            </div>
            <ShieldCheck className="size-5 text-accent-text" aria-hidden="true" />
          </div>

          {!concentration.available ? (
            <p className="mt-5 rounded-xl bg-background p-4 text-sm leading-6 text-text-secondary">{concentration.note}</p>
          ) : !concentration.coverage_reliable ? (
            <div className="mt-5 rounded-xl border border-warning/30 bg-warning/10 p-4">
              <p className="text-sm font-medium text-text-primary">Coverage is too thin for a reliable concentration read</p>
              <p className="mt-1 text-xs leading-5 text-text-secondary">{concentration.note}</p>
            </div>
          ) : (
            <>
              <div className="mt-5 rounded-xl bg-background p-4">
                <p className="text-xs text-text-secondary">Top broker share</p>
                <p className="mt-1 text-2xl font-semibold text-text-primary">{topBrokerShare === null ? "—" : `${(topBrokerShare * 100).toFixed(0)}%`}</p>
                <p className="mt-1 text-xs text-text-secondary">Across {concentration.top_brokers.length} leading brokers</p>
              </div>

              <div className="mt-4 divide-y divide-border">
                {concentration.top_brokers.slice(0, 5).map((broker) => (
                  <div key={broker.broker_id} className="flex items-center justify-between gap-4 py-3 text-sm">
                    <div className="min-w-0">
                      <p className="truncate font-medium text-text-primary">{broker.broker_name || `Broker ${broker.broker_id}`}</p>
                      <p className="mt-0.5 text-xs text-text-secondary">Broker #{broker.broker_id}</p>
                    </div>
                    <div className="text-right">
                      <p className="font-semibold tabular-nums text-text-primary">
                        {broker.fraction_of_sector_turnover === null ? "—" : `${(Number(broker.fraction_of_sector_turnover) * 100).toFixed(1)}%`}
                      </p>
                      <p className="mt-0.5 text-xs text-text-secondary">sector turnover</p>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </article>
      </section>

      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="border-b border-border px-5 py-5 sm:px-6">
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-secondary">Constituent intelligence</p>
          <h2 className="mt-1 text-xl font-semibold text-text-primary">Stocks in {sector.sector}</h2>
          <p className="mt-1 max-w-2xl text-sm text-text-secondary">Review the stocks driving this sector, their current price move and any active validated signal, then open the full stock analysis workspace.</p>
        </div>
        <SectorStocksPanel sector={sector.sector} />
      </section>
    </div>
  );
}
