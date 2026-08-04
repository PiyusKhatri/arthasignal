import Link from "next/link";
import { MarketPulseRow } from "@/components/dashboard/market-pulse-row";
import { PaperTradeBanner } from "@/components/dashboard/paper-trade-banner";
import { SectorPerformanceList } from "@/components/dashboard/sector-performance-list";
import { TodaysSignalsRow } from "@/components/dashboard/todays-signals-row";
import { WatchlistTable } from "@/components/dashboard/watchlist-table";
import {
  WATCHLIST_SYMBOLS,
  getActiveSignals,
  getMarketPulseData,
  getSectorPerformance,
  getWatchlistData,
} from "@/lib/market-data";

export default async function DashboardPage() {
  const [pulse, activeSignals, sectors, watchlist] = await Promise.all([
    getMarketPulseData(),
    getActiveSignals(),
    getSectorPerformance(),
    getWatchlistData(WATCHLIST_SYMBOLS),
  ]);

  const sortedSectors = sectors ?? [];
  const topSector = sortedSectors.find((sector) => sector.market_cap_weighted_percent_change !== null) ?? null;

  return (
    <div className="flex flex-col gap-8">
      <PaperTradeBanner />

      <section>
        <h1 className="text-lg font-semibold text-text-primary">Market pulse</h1>
        <div className="mt-4">
          <MarketPulseRow pulse={pulse} topSector={topSector} />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-text-primary">Today&apos;s signals</h2>
        <div className="mt-4">
          <TodaysSignalsRow signals={activeSignals?.signals ?? []} />
        </div>
      </section>

      <section className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <h2 className="text-lg font-semibold text-text-primary">Watchlist</h2>
          <div className="mt-4">
            <WatchlistTable stocks={watchlist} />
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-text-primary">Sector performance</h2>
            <Link href="/sectors" className="text-xs font-medium text-accent-text hover:underline">
              View all
            </Link>
          </div>
          <div className="mt-4">
            <SectorPerformanceList sectors={sortedSectors} />
          </div>
        </div>
      </section>
    </div>
  );
}
