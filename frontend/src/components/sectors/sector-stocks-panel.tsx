"use client";

import Link from "next/link";
import { ArrowUpRight, RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { getSectorStocks, type SectorStock } from "@/lib/market-data";
import { plainLanguageSignalLabel } from "@/lib/signal-labels";
import { NEUTRAL_TIER_CLASS, signalStatusLabel, signalStatusTitle } from "@/lib/signal-tiers";

function formatPrice(value: string | null): string {
  if (value === null) return "—";
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatSignedPercent(value: string | null): string {
  if (value === null) return "—";
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

function Change({ value }: { value: string | null }) {
  const num = value === null ? null : Number(value);
  const up = num !== null && num >= 0;
  return (
    <span className={`inline-flex items-center gap-1 font-semibold ${num === null ? "text-text-secondary" : up ? "text-success-text" : "text-danger-text"}`}>
      {num !== null ? up ? <TrendingUp className="size-3.5" aria-hidden="true" /> : <TrendingDown className="size-3.5" aria-hidden="true" /> : null}
      {formatSignedPercent(value)}
    </span>
  );
}

function SignalBadge({ stock }: { stock: SectorStock }) {
  if (!stock.active_signal) return <span className="text-xs text-text-secondary">No active signal</span>;
  return (
    <span
      title={signalStatusTitle(stock.active_signal.signal_name, stock.active_signal.validation)}
      className={`inline-flex max-w-full items-center rounded-md px-2 py-1 text-xs font-medium ${NEUTRAL_TIER_CLASS}`}
    >
      <span className="truncate">{plainLanguageSignalLabel(stock.active_signal.signal_name)}</span>
      <span className="ml-1 shrink-0 opacity-70">· {signalStatusLabel(stock.active_signal.tier, stock.active_signal.validation)}</span>
    </span>
  );
}

export function SectorStocksPanel({ sector }: { sector: string }) {
  const [stocks, setStocks] = useState<SectorStock[] | null>(null);
  const [error, setError] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    setStocks(null);

    getSectorStocks(sector)
      .then((data) => {
        if (!cancelled) setStocks(data);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [reloadKey, sector]);

  const sortedStocks = useMemo(() => {
    if (!stocks) return null;
    return [...stocks].sort((a, b) => Number(b.percent_change ?? Number.NEGATIVE_INFINITY) - Number(a.percent_change ?? Number.NEGATIVE_INFINITY));
  }, [stocks]);

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3 px-5 py-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-medium text-text-primary">Could not load constituent stocks</p>
          <p className="mt-1 text-xs text-text-secondary">The sector summary above is still available.</p>
        </div>
        <button
          type="button"
          onClick={() => setReloadKey((value) => value + 1)}
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs font-medium text-text-primary hover:border-accent-primary-light"
        >
          <RefreshCw className="size-3.5" aria-hidden="true" /> Retry
        </button>
      </div>
    );
  }

  if (sortedStocks === null) {
    return (
      <div className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="h-24 animate-pulse rounded-xl border border-border bg-card" />
        ))}
      </div>
    );
  }

  if (sortedStocks.length === 0) {
    return <p className="px-5 py-6 text-sm text-text-secondary">No active equity symbols found in this sector.</p>;
  }

  const advancers = sortedStocks.filter((stock) => Number(stock.percent_change ?? 0) > 0).length;
  const decliners = sortedStocks.filter((stock) => Number(stock.percent_change ?? 0) < 0).length;
  const signalCount = sortedStocks.filter((stock) => stock.active_signal !== null).length;

  return (
    <div className="p-4 sm:p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-secondary">Constituent stocks</p>
          <h4 className="mt-1 text-base font-semibold text-text-primary">{sector}</h4>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-success/10 px-2.5 py-1 font-medium text-success-text">{advancers} advancing</span>
          <span className="rounded-full bg-danger/10 px-2.5 py-1 font-medium text-danger-text">{decliners} declining</span>
          <span className="rounded-full bg-card px-2.5 py-1 font-medium text-text-secondary">{signalCount} active signals</span>
        </div>
      </div>

      <div className="grid gap-3 md:hidden">
        {sortedStocks.map((stock) => (
          <Link
            key={stock.symbol}
            href={`/stock/${stock.symbol}`}
            className="rounded-xl border border-border bg-card p-4 transition-colors hover:border-accent-primary-light"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-text-primary">{stock.symbol}</span>
                  <ArrowUpRight className="size-3.5 text-text-secondary" aria-hidden="true" />
                </div>
                <p className="mt-0.5 truncate text-xs text-text-secondary">{stock.company_name}</p>
              </div>
              <Change value={stock.percent_change} />
            </div>
            <div className="mt-3 flex items-end justify-between gap-3 border-t border-border pt-3">
              <div>
                <p className="text-[10px] uppercase tracking-[0.12em] text-text-secondary">Price</p>
                <p className="mt-1 text-sm font-semibold text-text-primary">Rs {formatPrice(stock.latest_close)}</p>
              </div>
              <div className="max-w-[65%] text-right"><SignalBadge stock={stock} /></div>
            </div>
          </Link>
        ))}
      </div>

      <div className="hidden overflow-x-auto rounded-xl border border-border bg-card md:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[11px] uppercase tracking-[0.1em] text-text-secondary">
              <th className="px-4 py-3 font-medium">Stock</th>
              <th className="px-4 py-3 font-medium">Price</th>
              <th className="px-4 py-3 font-medium">Change</th>
              <th className="px-4 py-3 font-medium">Signal</th>
              <th className="px-4 py-3 text-right font-medium">Analysis</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sortedStocks.map((stock) => (
              <tr key={stock.symbol} className="transition-colors hover:bg-background/70">
                <td className="px-4 py-3">
                  <p className="font-semibold text-text-primary">{stock.symbol}</p>
                  <p className="mt-0.5 max-w-64 truncate text-xs text-text-secondary">{stock.company_name}</p>
                </td>
                <td className="px-4 py-3 font-medium tabular-nums text-text-primary">Rs {formatPrice(stock.latest_close)}</td>
                <td className="px-4 py-3 tabular-nums"><Change value={stock.percent_change} /></td>
                <td className="px-4 py-3"><SignalBadge stock={stock} /></td>
                <td className="px-4 py-3 text-right">
                  <Link href={`/stock/${stock.symbol}`} className="inline-flex items-center gap-1 text-xs font-medium text-accent-text hover:underline">
                    Open stock <ArrowUpRight className="size-3.5" aria-hidden="true" />
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
