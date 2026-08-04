import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import { getAccessToken } from "@/lib/auth-proxy";

export async function GET() {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const backendResponse = await fetch(`${getApiBaseUrl()}/portfolio/summary`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
