import { SectorRankTable } from "@/components/sectors/sector-rank-table";
import { getSectorPerformance } from "@/lib/market-data";

export default async function SectorsPage() {
  const sectors = await getSectorPerformance();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold text-text-primary">Sector performance</h1>
        <p className="mt-1 text-sm text-text-secondary">
          Ranked by market-cap-weighted % change, best to worst performing today. Click a sector to see its
          constituent stocks.
        </p>
      </div>

      <SectorRankTable sectors={sectors ?? []} />
    </div>
  );
}
