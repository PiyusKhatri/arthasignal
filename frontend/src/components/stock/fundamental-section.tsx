import { StatCard } from "@/components/dashboard/stat-card";
import type { StockFundamental } from "@/lib/market-data";

function formatNumber(value: string | null, digits = 2): string {
  if (value === null) {
    return "—";
  }
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function formatPercent(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(1)}%`;
}

function formatMarketCap(value: string | null): string {
  if (value === null) {
    return "—";
  }
  const num = Number(value);
  if (num >= 1_000_000_000) {
    return `Rs ${(num / 1_000_000_000).toFixed(2)}B`;
  }
  return `Rs ${(num / 1_000_000).toFixed(2)}M`;
}

export function FundamentalSection({ fundamental }: { fundamental: StockFundamental }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">
        FY {fundamental.fiscal_year} · reported {fundamental.reported_date}
      </p>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <StatCard label="EPS" value={`Rs ${formatNumber(fundamental.eps)}`} />
        <StatCard
          label="P/E ratio"
          value={formatNumber(fundamental.pe_ratio)}
          meta={fundamental.sector_avg_pe !== null ? `Sector avg ${formatNumber(fundamental.sector_avg_pe)}` : undefined}
        />
        <StatCard
          label="P/B ratio"
          value={formatNumber(fundamental.pb_ratio)}
          meta={fundamental.sector_avg_pb !== null ? `Sector avg ${formatNumber(fundamental.sector_avg_pb)}` : undefined}
        />
        <StatCard label="Book value" value={`Rs ${formatNumber(fundamental.book_value)}`} />
        <StatCard label="Market cap" value={formatMarketCap(fundamental.market_capitalization)} />
        <StatCard
          label="Dividend"
          value={fundamental.dividend_percent !== null ? `${formatNumber(fundamental.dividend_percent, 2)}%` : "—"}
          meta={fundamental.dividend_fiscal_year ? `FY ${fundamental.dividend_fiscal_year}` : undefined}
        />
        <StatCard label="Payout ratio" value={fundamental.payout_ratio !== null ? `${formatNumber(fundamental.payout_ratio, 1)}%` : "—"} />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-text-primary">Sector-relative valuation</h3>
        {fundamental.sector_pe_relative_percent !== null ? (
          <p className="mt-2 text-sm text-text-primary">
            Trading at a P/E of {formatNumber(fundamental.pe_ratio)}, which is{" "}
            <span className="font-medium">{formatPercent(fundamental.sector_pe_relative_percent)}</span>{" "}
            {Number(fundamental.sector_pe_relative_percent) >= 0 ? "above" : "below"} the sector average of{" "}
            {formatNumber(fundamental.sector_avg_pe)}.
          </p>
        ) : (
          <p className="mt-2 text-sm text-text-secondary">Not enough sector data to compute a relative valuation.</p>
        )}
      </div>
    </div>
  );
}
