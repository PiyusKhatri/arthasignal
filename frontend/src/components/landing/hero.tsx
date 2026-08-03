import Link from "next/link";
import { HeroStockCard } from "@/components/landing/hero-stock-card";
import type { StockSignals, StockSummary } from "@/lib/landing-data";

export function Hero({ stock }: { stock: { summary: StockSummary; signals: StockSignals } | null }) {
  return (
    <section className="mx-auto grid max-w-7xl items-center gap-10 px-4 pt-16 pb-20 sm:px-6 lg:grid-cols-2 lg:gap-16 lg:pt-24">
      <div>
        <h1 className="max-w-xl text-4xl font-semibold tracking-tighter text-text-primary sm:text-5xl lg:text-6xl">
          NEPSE analysis, backed by data.
        </h1>
        <p className="mt-5 max-w-md text-base leading-relaxed text-text-secondary">
          Every signal is backtested against real NEPSE price history and reported with its actual win rate, not a
          guess.
        </p>
        <Link
          href="/signup"
          className="mt-8 inline-block rounded-md bg-accent-primary px-6 py-3 text-sm font-medium text-white transition-colors hover:bg-accent-primary-light"
        >
          Get started
        </Link>
      </div>

      <div className="flex justify-center lg:justify-end">
        {stock ? (
          <HeroStockCard summary={stock.summary} signals={stock.signals} />
        ) : (
          <div className="w-full max-w-sm rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
            Live stock data is temporarily unavailable.
          </div>
        )}
      </div>
    </section>
  );
}
