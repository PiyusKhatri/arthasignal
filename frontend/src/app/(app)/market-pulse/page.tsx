import { LivePriceBadge } from "@/components/charts/live-price-badge";
import { TimeframeChart } from "@/components/charts/timeframe-chart";
import { MarketPulseRow } from "@/components/dashboard/market-pulse-row";
import { PaperTradeBanner } from "@/components/dashboard/paper-trade-banner";
import { MarketIntelligenceOverview } from "@/components/intelligence/market-intelligence-overview";
import { StockScreener } from "@/components/intelligence/stock-screener";
import { getMarketIntelligence, getMarketPulseData, getSectorPerformance } from "@/lib/market-data";

export default async function MarketIntelligencePage() {
  const [intelligence, pulse, sectors] = await Promise.all([
    getMarketIntelligence(),
    getMarketPulseData(),
    getSectorPerformance(),
  ]);

  const sortedSectors = sectors ?? [];
  const topSector = sortedSectors.find((sector) => sector.market_cap_weighted_percent_change !== null) ?? null;
  const contextUnavailable = [pulse === null ? "market pulse" : null, sectors === null ? "sector performance" : null].filter(
    (value): value is string => value !== null,
  );

  return (
    <div className="flex flex-col gap-8">
      <PaperTradeBanner />

      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-accent-text">Market decision layer</p>
          <h1 className="mt-1 text-2xl font-semibold text-text-primary">Market Intelligence</h1>
          <p className="mt-2 max-w-3xl text-sm text-text-secondary">
            Rank NEPSE opportunities with the same Artha Score framework used on individual stocks, then validate the setup against live market breadth, sector strength and index context.
          </p>
        </div>
        <p className="text-xs text-text-secondary">
          {intelligence?.as_of_date ? `Intelligence snapshot: ${intelligence.as_of_date}` : "Latest validated session"}
        </p>
      </header>

      {intelligence ? (
        <MarketIntelligenceOverview intelligence={intelligence} />
      ) : (
        <section className="rounded-xl border border-warning/30 bg-warning/10 p-5">
          <h2 className="text-base font-semibold text-text-primary">Market intelligence is temporarily unavailable.</h2>
          <p className="mt-1 text-sm text-text-secondary">
            The dashboard will not infer a neutral or bearish market state from a failed intelligence request. Live context below remains independent when available.
          </p>
        </section>
      )}

      <section aria-labelledby="live-market-context-title">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Cross-check</p>
            <h2 id="live-market-context-title" className="mt-1 text-lg font-semibold text-text-primary">Live market context</h2>
          </div>
          {contextUnavailable.length > 0 ? (
            <p className="text-xs text-warning-text">Unavailable: {contextUnavailable.join(", ")}</p>
          ) : null}
        </div>
        <div className="mt-4">
          <MarketPulseRow pulse={pulse} topSector={topSector} />
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Index structure</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">NEPSE Index</h2>
          </div>
          <LivePriceBadge target={{ kind: "index" }} />
        </div>
        <div className="mt-4">
          <TimeframeChart target={{ kind: "index" }} />
        </div>
      </section>

      {intelligence ? (
        <StockScreener stocks={intelligence.stocks} />
      ) : (
        <section className="rounded-xl border border-border bg-card p-5">
          <h2 className="text-lg font-semibold text-text-primary">Artha stock screener</h2>
          <p className="mt-2 text-sm text-text-secondary">
            Screening is unavailable until the market intelligence dataset is reachable. No stocks are being hidden or treated as low quality because of the outage.
          </p>
        </section>
      )}
    </div>
  );
}
