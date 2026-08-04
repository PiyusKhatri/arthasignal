import { notFound } from "next/navigation";
import { TimeframeChart } from "@/components/charts/timeframe-chart";
import { FundamentalSection } from "@/components/stock/fundamental-section";
import { SignalsSection } from "@/components/stock/signals-section";
import { StockHeader } from "@/components/stock/stock-header";
import { TechnicalAnalysisSection } from "@/components/stock/technical-analysis-section";
import { getStockFundamental, getStockSignals, getStockSummary, getStockTechnical } from "@/lib/market-data";
import { isSymbolWatched } from "@/lib/watchlist-data";

export default async function StockDetailPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: rawSymbol } = await params;
  const symbol = rawSymbol.toUpperCase();

  const [summary, technical, signals, fundamental, watchStatus] = await Promise.all([
    getStockSummary(symbol),
    getStockTechnical(symbol),
    getStockSignals(symbol),
    getStockFundamental(symbol),
    isSymbolWatched(symbol),
  ]);

  if (!summary) {
    notFound();
  }

  return (
    <div className="flex flex-col gap-8">
      <StockHeader summary={summary} isWatching={watchStatus.isWatching} isAuthenticated={watchStatus.isAuthenticated} />

      <section>
        <h2 className="text-lg font-semibold text-text-primary">Price chart</h2>
        <div className="mt-4">
          <TimeframeChart target={{ kind: "stock", symbol }} />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-text-primary">Signals</h2>
        <div className="mt-4">
          {signals ? (
            <SignalsSection signals={signals.signals} />
          ) : (
            <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
              Signal data is temporarily unavailable.
            </div>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-text-primary">Technical analysis</h2>
        <div className="mt-4">
          {technical ? (
            <TechnicalAnalysisSection technical={technical} />
          ) : (
            <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
              Technical data is temporarily unavailable.
            </div>
          )}
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-text-primary">Fundamentals</h2>
        <div className="mt-4">
          {fundamental ? (
            <FundamentalSection fundamental={fundamental} />
          ) : (
            <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
              Fundamental data is temporarily unavailable.
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
