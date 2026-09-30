import { authenticatedBackendFetch, toNextResponse } from "@/lib/server-auth";

export async function POST(request: Request) {
  const body = await request.json();
  const result = await authenticatedBackendFetch("/portfolio/holdings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return toNextResponse(result);
}
