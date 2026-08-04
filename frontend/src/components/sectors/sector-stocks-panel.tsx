"use client";

import { TrendingDown, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";
import { getSectorStocks, type SectorStock } from "@/lib/market-data";
import { isHighConfidenceTier, tierLabel } from "@/lib/signal-tiers";

function formatPrice(value: string | null): string {
  if (value === null) {
    return "—";
  }
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatSignedPercent(value: string | null): string {
  if (value === null) {
    return "No data";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function SectorStocksPanel({ sector }: { sector: string }) {
  const [stocks, setStocks] = useState<SectorStock[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    getSectorStocks(sector)
      .then((data) => {
        if (!cancelled) {
          setStocks(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(true);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sector]);

  if (error) {
    return <p className="px-4 py-4 text-sm text-text-secondary">Could not load constituent stocks for this sector.</p>;
  }

  if (stocks === null) {
    return <p className="px-4 py-4 text-sm text-text-secondary">Loading constituent stocks…</p>;
  }

  if (stocks.length === 0) {
    return <p className="px-4 py-4 text-sm text-text-secondary">No active equity symbols found in this sector.</p>;
  }

  return (
    <div className="overflow-x-auto px-4 py-4">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-text-secondary">
            <th className="py-2 pr-4 font-medium">Symbol</th>
            <th className="py-2 pr-4 font-medium">Price</th>
            <th className="py-2 pr-4 font-medium">Change</th>
            <th className="py-2 pr-4 font-medium">Active signal</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {stocks.map((stock) => {
            const changeNum = stock.percent_change !== null ? Number(stock.percent_change) : null;
            const isUp = changeNum !== null && changeNum >= 0;
            return (
              <tr key={stock.symbol}>
                <td className="py-2 pr-4">
                  <p className="font-medium text-text-primary">{stock.symbol}</p>
                  <p className="text-xs text-text-secondary">{stock.company_name}</p>
                </td>
                <td className="py-2 pr-4 text-text-primary">{formatPrice(stock.latest_close)}</td>
                <td className="py-2 pr-4">
                  <span
                    className={`flex items-center gap-1 font-medium ${
                      changeNum === null ? "text-text-secondary" : isUp ? "text-success-text" : "text-danger-text"
                    }`}
                  >
                    {changeNum !== null ? (
                      isUp ? (
                        <TrendingUp className="size-3.5" aria-hidden="true" />
                      ) : (
                        <TrendingDown className="size-3.5" aria-hidden="true" />
                      )
                    ) : null}
                    {formatSignedPercent(stock.percent_change)}
                  </span>
                </td>
                <td className="py-2 pr-4">
                  {stock.active_signal ? (
                    <span
                      className={`inline-block rounded-md px-2 py-0.5 text-xs font-medium ${
                        isHighConfidenceTier(stock.active_signal.tier)
                          ? "bg-success/10 text-success-text"
                          : "bg-warning/10 text-warning-text"
                      }`}
                    >
                      {stock.active_signal.signal_name} · {tierLabel(stock.active_signal.tier)}
                    </span>
                  ) : (
                    <span className="text-xs text-text-secondary">No active signal</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
