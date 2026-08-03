import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";

export async function POST(request: Request) {
  const body = await request.json();

  const backendResponse = await fetch(`${getApiBaseUrl()}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
