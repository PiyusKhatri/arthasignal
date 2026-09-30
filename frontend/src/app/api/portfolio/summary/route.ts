import { authenticatedBackendFetch, toNextResponse } from "@/lib/server-auth";

export async function GET() {
  const result = await authenticatedBackendFetch("/portfolio/summary", { cache: "no-store" });
  return toNextResponse(result);
}
