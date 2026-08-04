"use client";

import { useState } from "react";
import Link from "next/link";
import { TrendingDown, TrendingUp, X } from "lucide-react";
import type { WatchlistItem } from "@/lib/watchlist-data";
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

export function WatchlistTable({ items, onChanged }: { items: WatchlistItem[]; onChanged: () => void }) {
  const [removingSymbol, setRemovingSymbol] = useState<string | null>(null);

  async function handleRemove(symbol: string) {
    setRemovingSymbol(symbol);
    try {
      const response = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}`, { method: "DELETE" });
      if (response.ok) {
        onChanged();
      }
    } finally {
      setRemovingSymbol(null);
    }
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-secondary">
            <th className="px-4 py-3 font-medium">Symbol</th>
            <th className="px-4 py-3 font-medium">Price</th>
            <th className="px-4 py-3 font-medium">Change</th>
            <th className="px-4 py-3 font-medium">Active signal</th>
            <th className="px-4 py-3 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => {
            const changeNum = item.percent_change !== null ? Number(item.percent_change) : null;
            const isUp = changeNum !== null && changeNum >= 0;
            return (
              <tr key={item.symbol}>
                <td className="px-4 py-3">
                  <Link href={`/stock/${item.symbol}`} className="font-medium text-text-primary hover:underline">
                    {item.symbol}
                  </Link>
                  <p className="text-xs text-text-secondary">{item.company_name}</p>
                </td>
                <td className="px-4 py-3 text-text-primary">{formatPrice(item.latest_close)}</td>
                <td className="px-4 py-3">
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
                    {formatSignedPercent(item.percent_change)}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {item.active_signal ? (
                    <span
                      className={`inline-block rounded-md px-2 py-0.5 text-xs font-medium ${
                        isHighConfidenceTier(item.active_signal.tier)
                          ? "bg-success/10 text-success-text"
                          : "bg-warning/10 text-warning-text"
                      }`}
                    >
                      {item.active_signal.signal_name} · {tierLabel(item.active_signal.tier)}
                    </span>
                  ) : (
                    <span className="text-xs text-text-secondary">No active signal</span>
                  )}
                </td>
                <td className="px-4 py-3">
                  <button
                    type="button"
                    onClick={() => handleRemove(item.symbol)}
                    disabled={removingSymbol === item.symbol}
                    aria-label={`Remove ${item.symbol} from watchlist`}
                    className="rounded-md p-1.5 text-text-secondary hover:bg-danger/10 hover:text-danger-text disabled:opacity-60"
                  >
                    <X className="size-4" aria-hidden="true" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
