import { NextResponse, type NextRequest } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import {
  ACCESS_TOKEN_COOKIE,
  ACCESS_TOKEN_MAX_AGE_SECONDS,
  authCookieOptions,
  REFRESH_TOKEN_COOKIE,
  REFRESH_TOKEN_MAX_AGE_SECONDS,
} from "@/lib/auth-cookies";

const PROTECTED_PATHS = ["/portfolio", "/watchlist"];

type AuthTokens = {
  access_token: string;
  refresh_token: string;
};

function redirectToLogin(request: NextRequest, clearCookies = false) {
  const loginUrl = new URL("/login", request.url);
  loginUrl.searchParams.set("next", request.nextUrl.pathname);
  const response = NextResponse.redirect(loginUrl);
  if (clearCookies) {
    response.cookies.set(ACCESS_TOKEN_COOKIE, "", authCookieOptions(0));
    response.cookies.set(REFRESH_TOKEN_COOKIE, "", authCookieOptions(0));
  }
  return response;
}

export async function proxy(request: NextRequest) {
  const isProtected = PROTECTED_PATHS.some(
    (path) => request.nextUrl.pathname === path || request.nextUrl.pathname.startsWith(`${path}/`),
  );

  if (!isProtected) {
    return NextResponse.next();
  }

  if (request.cookies.has(ACCESS_TOKEN_COOKIE)) {
    return NextResponse.next();
  }

  const refreshToken = request.cookies.get(REFRESH_TOKEN_COOKIE)?.value;
  if (!refreshToken) {
    return redirectToLogin(request);
  }

  try {
    const backendResponse = await fetch(`${getApiBaseUrl()}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!backendResponse.ok) {
      return redirectToLogin(request, true);
    }

    const tokens = (await backendResponse.json()) as AuthTokens;
    request.cookies.set(ACCESS_TOKEN_COOKIE, tokens.access_token);
    request.cookies.set(REFRESH_TOKEN_COOKIE, tokens.refresh_token);

    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("cookie", request.cookies.toString());
    const response = NextResponse.next({ request: { headers: requestHeaders } });
    response.cookies.set(
      ACCESS_TOKEN_COOKIE,
      tokens.access_token,
      authCookieOptions(ACCESS_TOKEN_MAX_AGE_SECONDS),
    );
    response.cookies.set(
      REFRESH_TOKEN_COOKIE,
      tokens.refresh_token,
      authCookieOptions(REFRESH_TOKEN_MAX_AGE_SECONDS),
    );
    return response;
  } catch {
    return redirectToLogin(request, true);
  }
}

export const config = {
  matcher: ["/portfolio/:path*", "/watchlist/:path*"],
};
