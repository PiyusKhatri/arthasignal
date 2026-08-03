import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import {
  ACCESS_TOKEN_COOKIE,
  ACCESS_TOKEN_MAX_AGE_SECONDS,
  authCookieOptions,
  REFRESH_TOKEN_COOKIE,
  REFRESH_TOKEN_MAX_AGE_SECONDS,
} from "@/lib/auth-cookies";

export async function POST(request: Request) {
  const body = await request.json();

  const backendResponse = await fetch(`${getApiBaseUrl()}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await backendResponse.json();

  if (!backendResponse.ok) {
    return NextResponse.json(data, { status: backendResponse.status });
  }

  const response = NextResponse.json({ status: "ok" });
  response.cookies.set(ACCESS_TOKEN_COOKIE, data.access_token, authCookieOptions(ACCESS_TOKEN_MAX_AGE_SECONDS));
  response.cookies.set(REFRESH_TOKEN_COOKIE, data.refresh_token, authCookieOptions(REFRESH_TOKEN_MAX_AGE_SECONDS));
  return response;
}
