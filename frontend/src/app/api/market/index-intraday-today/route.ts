import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function GET() {
  const backendResponse = await fetch(`${getApiBaseUrl()}/market/index-intraday-today`, { cache: "no-store" });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
