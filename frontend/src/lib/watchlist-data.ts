import { cookies } from "next/headers";
import { getApiBaseUrl } from "@/lib/api-config";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth-cookies";

export type WatchlistActiveSignal = {
  signal_name: string;
  tier: string | null;
  avg_win_rate_minus_baseline: string | null;
};

export type WatchlistItem = {
  symbol: string;
  company_name: string;
  latest_close: string | null;
  percent_change: string | null;
  active_signal: WatchlistActiveSignal | null;
  added_at: string;
};

export async function getWatchlist(): Promise<{ items: WatchlistItem[] | null; unauthorized: boolean }> {
  const token = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
  if (!token) {
    return { items: null, unauthorized: true };
  }

  const response = await fetch(`${getApiBaseUrl()}/watchlist`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (response.status === 401) {
    return { items: null, unauthorized: true };
  }
  if (!response.ok) {
    return { items: null, unauthorized: false };
  }

  return { items: (await response.json()) as WatchlistItem[], unauthorized: false };
}

export async function isSymbolWatched(symbol: string): Promise<{ isWatching: boolean; isAuthenticated: boolean }> {
  const token = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
  if (!token) {
    return { isWatching: false, isAuthenticated: false };
  }

  const response = await fetch(`${getApiBaseUrl()}/watchlist`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (!response.ok) {
    return { isWatching: false, isAuthenticated: response.status !== 401 };
  }

  const items = (await response.json()) as WatchlistItem[];
  return { isWatching: items.some((item) => item.symbol === symbol), isAuthenticated: true };
}
