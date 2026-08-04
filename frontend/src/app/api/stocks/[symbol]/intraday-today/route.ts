import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function GET(_request: Request, { params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;

  const backendResponse = await fetch(`${getApiBaseUrl()}/stocks/${encodeURIComponent(symbol)}/intraday-today`, {
    cache: "no-store",
  });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
