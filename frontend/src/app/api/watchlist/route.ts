import { authenticatedBackendFetch, toNextResponse } from "@/lib/server-auth";

export async function GET() {
  const result = await authenticatedBackendFetch("/watchlist", { cache: "no-store" });
  return toNextResponse(result);
}

export async function POST(request: Request) {
  const body = await request.json();
  const result = await authenticatedBackendFetch("/watchlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return toNextResponse(result);
}
