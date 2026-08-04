import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function GET(_request: Request, { params }: { params: Promise<{ sector: string }> }) {
  const { sector } = await params;

  const backendResponse = await fetch(`${getApiBaseUrl()}/market/sectors/${encodeURIComponent(sector)}/stocks`, {
    cache: "no-store",
  });

  const data = await backendResponse.json();

  if (!backendResponse.ok) {
    return NextResponse.json(data, { status: backendResponse.status });
  }

  return NextResponse.json(data);
}
