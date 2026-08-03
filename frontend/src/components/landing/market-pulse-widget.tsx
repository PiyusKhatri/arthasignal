import type { MarketPulse } from "@/lib/market-data";

function formatSignedPercent(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

export function MarketPulseWidget({ pulse }: { pulse: MarketPulse | null }) {
  if (!pulse) {
    return (
      <section id="pulse" className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">Live market pulse</h2>
        <p className="mt-4 text-sm text-text-secondary">Market data is temporarily unavailable.</p>
      </section>
    );
  }

  const hasBreadth = pulse.advance_decline.total_symbols > 0;
  const turnoverRatio = pulse.turnover_trend.turnover_vs_trailing_avg_ratio
    ? Number(pulse.turnover_trend.turnover_vs_trailing_avg_ratio)
    : null;

  return (
    <section id="pulse" className="border-y border-border bg-card">
      <div className="mx-auto max-w-7xl px-4 py-20 sm:px-6">
        <h2 className="text-2xl font-semibold tracking-tight text-text-primary sm:text-3xl">Live market pulse</h2>
        <p className="mt-2 text-sm text-text-secondary">Pulled straight from the same feed powering every signal.</p>

        <div className="mt-10 grid grid-cols-1 divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          <div className="py-6 sm:px-8 sm:py-0 sm:first:pl-0">
            <p className="text-xs text-text-secondary">Advance / decline</p>
            {hasBreadth ? (
              <p className="mt-2 text-2xl font-semibold text-text-primary">
                <span className="text-success-text">{pulse.advance_decline.advances}</span>
                {" / "}
                <span className="text-danger-text">{pulse.advance_decline.declines}</span>
              </p>
            ) : (
              <p className="mt-2 text-sm text-text-secondary">No trades captured yet today.</p>
            )}
          </div>

          <div className="py-6 sm:px-8 sm:py-0">
            <p className="text-xs text-text-secondary">NEPSE index</p>
            {pulse.nepse_index?.current_value !== null && pulse.nepse_index?.current_value !== undefined ? (
              <p className="mt-2 text-2xl font-semibold text-text-primary">
                {pulse.nepse_index.current_value.toLocaleString("en-US", { maximumFractionDigits: 2 })}
                {pulse.nepse_index.percent_change !== null ? (
                  <span className={`ml-2 text-base font-medium ${pulse.nepse_index.percent_change >= 0 ? "text-success-text" : "text-danger-text"}`}>
                    {formatSignedPercent(pulse.nepse_index.percent_change)}
                  </span>
                ) : null}
              </p>
            ) : (
              <p className="mt-2 text-sm text-text-secondary">Index data unavailable.</p>
            )}
          </div>

          <div className="py-6 sm:px-8 sm:py-0">
            <p className="text-xs text-text-secondary">Turnover vs 20-day average</p>
            {turnoverRatio ? (
              <p className="mt-2 text-2xl font-semibold text-text-primary">{turnoverRatio.toFixed(2)}x</p>
            ) : (
              <p className="mt-2 text-sm text-text-secondary">Today&apos;s turnover isn&apos;t in yet.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
