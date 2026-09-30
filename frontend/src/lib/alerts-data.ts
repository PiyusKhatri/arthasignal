import { cookies } from "next/headers";
import { getApiBaseUrl } from "@/lib/api-config";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth-cookies";

export type PriceAlertCondition = "above" | "below";

export type PriceAlert = {
  id: number;
  symbol: string;
  condition: PriceAlertCondition;
  target_price: string;
  is_active: boolean;
  triggered_at: string | null;
  created_at: string;
};

export type SignalAlert = {
  id: number;
  symbol: string | null;
  signal_name: string;
  is_active: boolean;
  triggered_at: string | null;
  triggered_symbol: string | null;
  created_at: string;
};

async function fetchWithAuth<T>(path: string): Promise<{ data: T | null; unauthorized: boolean }> {
  const token = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
  if (!token) {
    return { data: null, unauthorized: true };
  }

  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (response.status === 401) {
    return { data: null, unauthorized: true };
  }
  if (!response.ok) {
    return { data: null, unauthorized: false };
  }

  return { data: (await response.json()) as T, unauthorized: false };
}

export async function getPriceAlerts(): Promise<{ alerts: PriceAlert[] | null; unauthorized: boolean }> {
  const { data, unauthorized } = await fetchWithAuth<PriceAlert[]>("/alerts/price");
  return { alerts: data, unauthorized };
}

export async function getSignalAlerts(): Promise<{ alerts: SignalAlert[] | null; unauthorized: boolean }> {
  const { data, unauthorized } = await fetchWithAuth<SignalAlert[]>("/alerts/signal");
  return { alerts: data, unauthorized };
}
