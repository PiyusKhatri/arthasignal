export const ACCESS_TOKEN_COOKIE = "arthasignal_access_token";
export const REFRESH_TOKEN_COOKIE = "arthasignal_refresh_token";

export const ACCESS_TOKEN_MAX_AGE_SECONDS = 15 * 60;
export const REFRESH_TOKEN_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

export function authCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
