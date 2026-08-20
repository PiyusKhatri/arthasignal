import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import {
  ACCESS_TOKEN_COOKIE,
  authCookieOptions,
  REFRESH_TOKEN_COOKIE,
} from "@/lib/auth-cookies";

export async function POST(request: Request) {
  const body = await request.json();

  const backendResponse = await fetch(`${getApiBaseUrl()}/auth/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await backendResponse.json();
  const response = NextResponse.json(data, { status: backendResponse.status });

  if (backendResponse.ok) {
    response.cookies.set(ACCESS_TOKEN_COOKIE, "", authCookieOptions(0));
    response.cookies.set(REFRESH_TOKEN_COOKIE, "", authCookieOptions(0));
  }

  return response;
}
