import { authenticatedBackendFetch, toNextResponse } from "@/lib/server-auth";

export async function DELETE(_request: Request, { params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  const result = await authenticatedBackendFetch(`/watchlist/${encodeURIComponent(symbol)}`, {
    method: "DELETE",
  });
  return toNextResponse(result);
}
