import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import { getAccessToken } from "@/lib/auth-proxy";

export async function DELETE(_request: Request, { params }: { params: Promise<{ symbol: string }> }) {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const { symbol } = await params;

  const backendResponse = await fetch(`${getApiBaseUrl()}/watchlist/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });

  if (backendResponse.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
