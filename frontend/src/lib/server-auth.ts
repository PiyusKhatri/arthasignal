import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import {
  ACCESS_TOKEN_COOKIE,
  ACCESS_TOKEN_MAX_AGE_SECONDS,
  authCookieOptions,
  REFRESH_TOKEN_COOKIE,
  REFRESH_TOKEN_MAX_AGE_SECONDS,
} from "@/lib/auth-cookies";

type AuthTokens = {
  access_token: string;
  refresh_token: string;
};

export type AuthenticatedFetchResult = {
  response: Response;
  tokens: AuthTokens | null;
  clearCookies: boolean;
};

async function refreshSession(refreshToken: string): Promise<AuthTokens | null> {
  try {
    const response = await fetch(`${getApiBaseUrl()}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as AuthTokens;
  } catch {
    return null;
  }
}

function unauthenticatedResponse(): Response {
  return new Response(JSON.stringify({ detail: "Not authenticated" }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

async function callBackend(path: string, accessToken: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(`${getApiBaseUrl()}${path}`, { ...init, headers, cache: init.cache ?? "no-store" });
}

export async function authenticatedBackendFetch(
  path: string,
  init: RequestInit = {},
): Promise<AuthenticatedFetchResult> {
  const store = await cookies();
  let accessToken = store.get(ACCESS_TOKEN_COOKIE)?.value ?? null;
  let refreshToken = store.get(REFRESH_TOKEN_COOKIE)?.value ?? null;
  let rotatedTokens: AuthTokens | null = null;

  if (!accessToken && refreshToken) {
    rotatedTokens = await refreshSession(refreshToken);
    if (!rotatedTokens) {
      return { response: unauthenticatedResponse(), tokens: null, clearCookies: true };
    }
    accessToken = rotatedTokens.access_token;
    refreshToken = rotatedTokens.refresh_token;
  }

  if (!accessToken) {
    return { response: unauthenticatedResponse(), tokens: null, clearCookies: false };
  }

  let response = await callBackend(path, accessToken, init);

  if (response.status === 401 && refreshToken && !rotatedTokens) {
    rotatedTokens = await refreshSession(refreshToken);
    if (!rotatedTokens) {
      return { response, tokens: null, clearCookies: true };
    }
    response = await callBackend(path, rotatedTokens.access_token, init);
  }

  return {
    response,
    tokens: rotatedTokens,
    clearCookies: response.status === 401,
  };
}

export async function toNextResponse(result: AuthenticatedFetchResult): Promise<NextResponse> {
  const contentType = result.response.headers.get("content-type") ?? "application/json";
  const body = result.response.status === 204 ? null : await result.response.text();
  const response = new NextResponse(body, {
    status: result.response.status,
    headers: body === null ? undefined : { "Content-Type": contentType },
  });

  if (result.clearCookies) {
    response.cookies.set(ACCESS_TOKEN_COOKIE, "", authCookieOptions(0));
    response.cookies.set(REFRESH_TOKEN_COOKIE, "", authCookieOptions(0));
  } else if (result.tokens) {
    response.cookies.set(
      ACCESS_TOKEN_COOKIE,
      result.tokens.access_token,
      authCookieOptions(ACCESS_TOKEN_MAX_AGE_SECONDS),
    );
    response.cookies.set(
      REFRESH_TOKEN_COOKIE,
      result.tokens.refresh_token,
      authCookieOptions(REFRESH_TOKEN_MAX_AGE_SECONDS),
    );
  }

  return response;
}
