import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function GET(request: Request, { params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const { searchParams } = new URL(request.url);
  const range = searchParams.get("range") ?? "6M";

  const backendResponse = await fetch(
    `${getApiBaseUrl()}/stocks/${encodeURIComponent(symbol)}/history?range=${encodeURIComponent(range)}`,
    { cache: "no-store" }
  );

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
