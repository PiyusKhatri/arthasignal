import { authenticatedBackendFetch, toNextResponse } from "@/lib/server-auth";

export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await request.json();
  const result = await authenticatedBackendFetch(`/portfolio/holdings/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return toNextResponse(result);
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await authenticatedBackendFetch(`/portfolio/holdings/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  return toNextResponse(result);
}
