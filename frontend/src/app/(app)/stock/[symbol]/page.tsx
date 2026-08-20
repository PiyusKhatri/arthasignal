import { notFound } from "next/navigation";
import { TimeframeChart } from "@/components/charts/timeframe-chart";
import { StockIntelligencePanel } from "@/components/intelligence/stock-intelligence-panel";
import { FundamentalSection } from "@/components/stock/fundamental-section";
import { SignalsSection } from "@/components/stock/signals-section";
import { StockHeader } from "@/components/stock/stock-header";
import { TechnicalAnalysisSection } from "@/components/stock/technical-analysis-section";
import {
  getStockFundamental,
  getStockIntelligence,
  getStockSignals,
  getStockSummary,
  getStockTechnical,
} from "@/lib/market-data";
import { isSymbolWatched } from "@/lib/watchlist-data";

export default async function StockDetailPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: rawSymbol } = await params;
  const symbol = rawSymbol.toUpperCase();

  const [summary, technical, signals, fundamental, intelligence, watchStatus] = await Promise.all([
    getStockSummary(symbol),
    getStockTechnical(symbol),
    getStockSignals(symbol),
    getStockFundamental(symbol),
    getStockIntelligence(symbol),
    isSymbolWatched(symbol),
  ]);

  if (!summary) {
    notFound();
  }

  const chartIntelligence = intelligence
    ? {
        score: intelligence.artha_score,
        rating: intelligence.rating,
        confidence: intelligence.confidence_level,
        asOfDate: intelligence.as_of_date,
        signals: intelligence.signals.map((signal) => ({
          signalName: signal.signal_name,
          status: signal.status,
          entryDate: signal.entry_date,
          direction: signal.direction,
        })),
      }
    : null;

  return (
    <div className="flex flex-col gap-8">
      <StockHeader summary={summary} isWatching={watchStatus.isWatching} isAuthenticated={watchStatus.isAuthenticated} />

      {intelligence ? (
        <StockIntelligencePanel intelligence={intelligence} />
      ) : (
        <section className="rounded-xl border border-warning/30 bg-warning/10 p-5">
          <h2 className="text-base font-semibold text-text-primary">Artha Intelligence is temporarily unavailable</h2>
          <p className="mt-1 text-sm text-text-secondary">
            Price, technical, signal and fundamental data below remain available. An unavailable intelligence response is not a negative market signal.
          </p>
        </section>
      )}

      <section>
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.16em] text-text-secondary">Analysis workspace</p>
            <h2 className="mt-1 text-lg font-semibold text-text-primary">Price & technical chart</h2>
          </div>
          <p className="max-w-xl text-right text-xs text-text-secondary">
            Draw, compare, replay and layer indicators directly on the same NEPSE price history used by Artha Intelligence.
          </p>
        </div>
        <div className="mt-4">
          <TimeframeChart target={{ kind: "stock", symbol }} artha={chartIntelligence} />
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
