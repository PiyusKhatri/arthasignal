import { cookies } from "next/headers";
import { ACCESS_TOKEN_COOKIE } from "@/lib/auth-cookies";

export async function getAccessToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(ACCESS_TOKEN_COOKIE)?.value ?? null;
}
