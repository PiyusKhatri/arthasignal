import Link from "next/link";
import type { StockSummary } from "@/lib/market-data";

function formatPercent(value: string | null): string {
  if (value === null) {
    return "0.00%";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
}

export function WatchlistTable({ stocks }: { stocks: StockSummary[] }) {
  if (stocks.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-sm text-text-secondary">
        Watchlist data is temporarily unavailable.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs text-text-secondary">
            <th className="px-4 py-3 font-medium">Symbol</th>
            <th className="px-4 py-3 font-medium">Price</th>
            <th className="px-4 py-3 font-medium">Change</th>
            <th className="px-4 py-3 font-medium">Volume</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {stocks.map((stock) => {
            const change = Number(stock.percent_change ?? 0);
            const isUp = change >= 0;
            return (
              <tr key={stock.symbol}>
                <td className="px-4 py-3">
                  <Link href={`/stock/${stock.symbol}`} className="font-medium text-text-primary hover:underline">
                    {stock.symbol}
                  </Link>
                  <p className="text-xs text-text-secondary">{stock.company_name}</p>
                </td>
                <td className="px-4 py-3 text-text-primary">
                  {Number(stock.latest_close).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </td>
                <td className={`px-4 py-3 font-medium ${isUp ? "text-success-text" : "text-danger-text"}`}>
                  {formatPercent(stock.percent_change)}
                </td>
                <td className="px-4 py-3 text-text-secondary">{stock.volume.toLocaleString("en-US")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
