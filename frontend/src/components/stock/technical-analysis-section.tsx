import type { StockTechnical } from "@/lib/market-data";

function formatValue(value: string | null, digits = 2): string {
  if (value === null) {
    return "—";
  }
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

type Metric = { label: string; value: string | null };

function IndicatorGroup({ title, metrics }: { title: string; metrics: Metric[] }) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        {metrics.map((metric) => (
          <div key={metric.label}>
            <dt className="text-xs text-text-secondary">{metric.label}</dt>
            <dd className="text-sm font-medium text-text-primary">{formatValue(metric.value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const CANDLESTICK_PATTERN_LABELS: { key: keyof StockTechnical; label: string }[] = [
  { key: "doji", label: "Doji" },
  { key: "marubozu_bullish", label: "Bullish marubozu" },
  { key: "marubozu_bearish", label: "Bearish marubozu" },
  { key: "hammer", label: "Hammer" },
  { key: "shooting_star", label: "Shooting star" },
  { key: "spinning_top", label: "Spinning top" },
  { key: "bullish_engulfing", label: "Bullish engulfing" },
  { key: "bearish_engulfing", label: "Bearish engulfing" },
  { key: "bullish_harami", label: "Bullish harami" },
  { key: "bearish_harami", label: "Bearish harami" },
  { key: "piercing_line", label: "Piercing line" },
  { key: "dark_cloud_cover", label: "Dark cloud cover" },
  { key: "tweezer_top", label: "Tweezer top" },
  { key: "tweezer_bottom", label: "Tweezer bottom" },
  { key: "morning_star", label: "Morning star" },
  { key: "evening_star", label: "Evening star" },
  { key: "three_white_soldiers", label: "Three white soldiers" },
  { key: "three_black_crows", label: "Three black crows" },
];

export function TechnicalAnalysisSection({ technical }: { technical: StockTechnical }) {
  const activePatterns = CANDLESTICK_PATTERN_LABELS.filter((pattern) => technical[pattern.key] === true);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-text-secondary">As of {technical.date}</p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <IndicatorGroup
          title="Trend"
          metrics={[
            { label: "SMA 20", value: technical.sma_20 },
            { label: "SMA 50", value: technical.sma_50 },
            { label: "SMA 100", value: technical.sma_100 },
            { label: "SMA 200", value: technical.sma_200 },
            { label: "EMA 20", value: technical.ema_20 },
            { label: "EMA 50", value: technical.ema_50 },
            { label: "MACD line", value: technical.macd_line },
            { label: "MACD signal", value: technical.macd_signal },
            { label: "MACD histogram", value: technical.macd_histogram },
            { label: "Pivot", value: technical.pivot },
            { label: "Resistance 1", value: technical.pivot_r1 },
            { label: "Resistance 2", value: technical.pivot_r2 },
            { label: "Resistance 3", value: technical.pivot_r3 },
            { label: "Support 1", value: technical.pivot_s1 },
            { label: "Support 2", value: technical.pivot_s2 },
            { label: "Support 3", value: technical.pivot_s3 },
            { label: "Fib 23.6%", value: technical.fib_236 },
            { label: "Fib 38.2%", value: technical.fib_382 },
            { label: "Fib 50%", value: technical.fib_50 },
            { label: "Fib 61.8%", value: technical.fib_618 },
            { label: "Fib 78.6%", value: technical.fib_786 },
          ]}
        />

        <IndicatorGroup
          title="Momentum"
          metrics={[
            { label: "RSI 14", value: technical.rsi_14 },
            { label: "Stochastic %K", value: technical.stochastic_k },
            { label: "Stochastic %D", value: technical.stochastic_d },
            { label: "CCI 20", value: technical.cci_20 },
            { label: "ROC 12", value: technical.roc_12 },
          ]}
        />

        <IndicatorGroup
          title="Volatility"
          metrics={[
            { label: "Bollinger upper", value: technical.bollinger_upper },
            { label: "Bollinger middle", value: technical.bollinger_middle },
            { label: "Bollinger lower", value: technical.bollinger_lower },
            { label: "ATR 14", value: technical.atr_14 },
            { label: "52-week high", value: technical.fifty_two_week_high },
            { label: "52-week low", value: technical.fifty_two_week_low },
          ]}
        />

        <IndicatorGroup
          title="Volume"
          metrics={[
            { label: "OBV", value: technical.obv },
            { label: "VWAP 20", value: technical.vwap_20 },
          ]}
        />
      </div>

      <div className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-text-primary">Candlestick patterns (latest candle)</h3>
        {activePatterns.length === 0 ? (
          <p className="mt-2 text-sm text-text-secondary">No candlestick pattern detected on the latest candle.</p>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {activePatterns.map((pattern) => (
              <span
                key={pattern.key as string}
                className="rounded-md bg-accent-text/10 px-2 py-0.5 text-xs font-medium text-accent-text"
              >
                {pattern.label}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
