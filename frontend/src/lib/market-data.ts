import { getApiBaseUrl } from "@/lib/api-config";

export const HERO_SYMBOL = "SBL";

export const WATCHLIST_SYMBOLS = ["NABIL", "SBL", "HDL", "CHCL"];

export type StockSummary = {
  symbol: string;
  company_name: string;
  sector: string;
  latest_date: string;
  latest_close: string;
  previous_close: string | null;
  percent_change: string | null;
  volume: number;
  day_high: string;
  day_low: string;
};

export type StockTechnical = {
  symbol: string;
  date: string;
  timeframe: string;
  sma_20: string | null;
  sma_50: string | null;
  sma_100: string | null;
  sma_200: string | null;
  ema_20: string | null;
  ema_50: string | null;
  rsi_14: string | null;
  macd_line: string | null;
  macd_signal: string | null;
  macd_histogram: string | null;
  stochastic_k: string | null;
  stochastic_d: string | null;
  cci_20: string | null;
  roc_12: string | null;
  bollinger_middle: string | null;
  bollinger_upper: string | null;
  bollinger_lower: string | null;
  atr_14: string | null;
  obv: string | null;
  vwap_20: string | null;
  fifty_two_week_high: string | null;
  fifty_two_week_low: string | null;
  pivot: string | null;
  pivot_r1: string | null;
  pivot_r2: string | null;
  pivot_r3: string | null;
  pivot_s1: string | null;
  pivot_s2: string | null;
  pivot_s3: string | null;
  fib_0: string | null;
  fib_236: string | null;
  fib_382: string | null;
  fib_50: string | null;
  fib_618: string | null;
  fib_786: string | null;
  fib_100: string | null;
  doji: boolean | null;
  marubozu_bullish: boolean | null;
  marubozu_bearish: boolean | null;
  hammer: boolean | null;
  shooting_star: boolean | null;
  spinning_top: boolean | null;
  bullish_engulfing: boolean | null;
  bearish_engulfing: boolean | null;
  bullish_harami: boolean | null;
  bearish_harami: boolean | null;
  piercing_line: boolean | null;
  dark_cloud_cover: boolean | null;
  tweezer_top: boolean | null;
  tweezer_bottom: boolean | null;
  morning_star: boolean | null;
  evening_star: boolean | null;
  three_white_soldiers: boolean | null;
  three_black_crows: boolean | null;
};

export type StockFundamental = {
  symbol: string;
  fiscal_year: string;
  eps: string | null;
  pe_ratio: string | null;
  pb_ratio: string | null;
  book_value: string | null;
  market_capitalization: string | null;
  reported_date: string;
  dividend_percent: string | null;
  dividend_fiscal_year: string | null;
  payout_ratio: string | null;
  sector_avg_pe: string | null;
  sector_avg_pb: string | null;
  sector_pe_relative_percent: string | null;
};

export type StockSignal = {
  signal_name: string;
  active: boolean;
  tier: string | null;
  avg_win_rate_minus_baseline: string | null;
  recommended_holding_period: string | null;
  cost_viability_note: string | null;
};

export type StockSignals = {
  symbol: string;
  as_of_date: string;
  signals: StockSignal[];
};

export type MarketPulse = {
  date: string;
  advance_decline: {
    advances: number;
    declines: number;
    unchanged: number;
    total_symbols: number;
    interpretation: string;
  };
  nepse_index: {
    current_value: number | null;
    percent_change: number | null;
    points_change: number | null;
  } | null;
  turnover_trend: {
    today_turnover: string | null;
    turnover_vs_trailing_avg_ratio: string | null;
  };
};

export type ActiveSignal = {
  symbol: string;
  company_name: string;
  sector: string;
  latest_close: string;
  percent_change: string | null;
  signal_name: string;
  tier: string | null;
  avg_win_rate_minus_baseline: string | null;
  recommended_holding_period: string | null;
};

export type ActiveSignalsResponse = {
  as_of_date: string | null;
  signals: ActiveSignal[];
};

export type SectorBrokerConcentration = {
  available: boolean;
  coverage_reliable?: boolean;
  symbols_covered: number;
  total_sector_symbols: number;
  floorsheet_turnover?: string;
  top_brokers: {
    broker_id: string;
    broker_name: string | null;
    total_contract_value: string;
    fraction_of_sector_turnover: string | null;
  }[];
  note: string;
};

export type SectorPerformance = {
  sector: string;
  advance_decline: {
    advances: number;
    declines: number;
    unchanged: number;
    total_symbols: number;
    advance_decline_ratio: string | null;
    interpretation: string;
    source: string;
  };
  sector_index: {
    index_name: string;
    source: string;
    current_value: string | null;
    percent_change: string | null;
    points_change: string | null;
  };
  market_cap_weighted_percent_change: string | null;
  market_cap_weighting: {
    symbols_used: number;
    symbols_excluded_no_market_cap: number;
  };
  turnover_trend: {
    today_turnover: string | null;
    trailing_20_day_avg_turnover: string | null;
    turnover_vs_trailing_avg_ratio: string | null;
  };
  broker_concentration: SectorBrokerConcentration;
};

export type SectorStock = {
  symbol: string;
  company_name: string;
  latest_close: string | null;
  percent_change: string | null;
  active_signal: {
    signal_name: string;
    tier: string | null;
    avg_win_rate_minus_baseline: string | null;
  } | null;
};

export async function getSectorStocks(sector: string): Promise<SectorStock[]> {
  const response = await fetch(`/api/sectors/${encodeURIComponent(sector)}/stocks`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to load stocks for sector: ${sector}`);
  }
  return (await response.json()) as SectorStock[];
}

async function fetchJson<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function getHeroStockData(): Promise<{ summary: StockSummary; signals: StockSignals } | null> {
  const [summary, signals] = await Promise.all([
    fetchJson<StockSummary>(`/stocks/${HERO_SYMBOL}/summary`),
    fetchJson<StockSignals>(`/stocks/${HERO_SYMBOL}/signals`),
  ]);

  if (!summary || !signals) {
    return null;
  }

  return { summary, signals };
}

export async function getMarketPulseData(): Promise<MarketPulse | null> {
  return fetchJson<MarketPulse>("/market/pulse");
}

export async function getActiveSignals(): Promise<ActiveSignalsResponse | null> {
  return fetchJson<ActiveSignalsResponse>("/market/active-signals");
}

export async function getSectorPerformance(): Promise<SectorPerformance[] | null> {
  return fetchJson<SectorPerformance[]>("/market/sectors");
}

export async function getStockSummary(symbol: string): Promise<StockSummary | null> {
  return fetchJson<StockSummary>(`/stocks/${symbol}/summary`);
}

export async function getStockTechnical(symbol: string): Promise<StockTechnical | null> {
  return fetchJson<StockTechnical>(`/stocks/${symbol}/technical`);
}

export async function getStockSignals(symbol: string): Promise<StockSignals | null> {
  return fetchJson<StockSignals>(`/stocks/${symbol}/signals`);
}

export async function getStockFundamental(symbol: string): Promise<StockFundamental | null> {
  return fetchJson<StockFundamental>(`/stocks/${symbol}/fundamental`);
}

export async function getWatchlistData(symbols: string[]): Promise<StockSummary[]> {
  const results = await Promise.all(symbols.map((symbol) => getStockSummary(symbol)));
  return results.filter((summary): summary is StockSummary => summary !== null);
}
