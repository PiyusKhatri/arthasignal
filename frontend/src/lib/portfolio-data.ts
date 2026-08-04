import { cookies } from "next/headers";
import { getApiBaseUrl } from "@/lib/api-config";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth-cookies";

export type HoldingSummary = {
  id: number;
  symbol: string;
  quantity: string;
  purchase_price: string;
  purchase_date: string;
  current_price: string | null;
  invested_amount: string;
  current_value: string | null;
  unrealized_pl: string | null;
  unrealized_pl_percent: string | null;
};

export type PortfolioSummary = {
  total_invested: string;
  total_current_value: string;
  total_unrealized_pl: string;
  total_unrealized_pl_percent: string | null;
  holdings: HoldingSummary[];
};

export type HoldingInput = {
  symbol: string;
  quantity: string;
  purchase_price: string;
  purchase_date: string;
};

export async function getPortfolioSummary(): Promise<{ summary: PortfolioSummary | null; unauthorized: boolean }> {
  const token = (await cookies()).get(ACCESS_TOKEN_COOKIE)?.value;
  if (!token) {
    return { summary: null, unauthorized: true };
  }

  const response = await fetch(`${getApiBaseUrl()}/portfolio/summary`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  if (response.status === 401) {
    return { summary: null, unauthorized: true };
  }
  if (!response.ok) {
    return { summary: null, unauthorized: false };
  }

  return { summary: (await response.json()) as PortfolioSummary, unauthorized: false };
}
