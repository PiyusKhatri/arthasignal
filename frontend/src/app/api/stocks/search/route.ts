import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const q = searchParams.get("q") ?? "";

  if (q.trim().length === 0) {
    return NextResponse.json([]);
  }

  const backendResponse = await fetch(`${getApiBaseUrl()}/stocks/search?q=${encodeURIComponent(q)}`, {
    cache: "no-store",
  });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
