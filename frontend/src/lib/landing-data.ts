import { getApiBaseUrl } from "@/lib/api-config";

export const HERO_SYMBOL = "SBL";

export type StockSummary = {
  symbol: string;
  company_name: string;
  sector: string;
  latest_date: string;
  latest_close: string;
  previous_close: string | null;
  percent_change: string | null;
};

export type StockSignal = {
  signal_name: string;
  active: boolean;
  tier: string | null;
  avg_win_rate_minus_baseline: string | null;
  recommended_holding_period: string | null;
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
    current_value: number;
    percent_change: number;
    points_change: number;
  } | null;
  turnover_trend: {
    today_turnover: string | null;
    turnover_vs_trailing_avg_ratio: string | null;
  };
};

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
