import { NextResponse } from "next/server";
import { getApiBaseUrl } from "@/lib/api-config";
import { getAccessToken } from "@/lib/auth-proxy";

export async function PUT(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const { id } = await params;
  const body = await request.json();

  const backendResponse = await fetch(`${getApiBaseUrl()}/portfolio/holdings/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  });

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const token = await getAccessToken();
  if (!token) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const { id } = await params;

  const backendResponse = await fetch(`${getApiBaseUrl()}/portfolio/holdings/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });

  if (backendResponse.status === 204) {
    return new NextResponse(null, { status: 204 });
  }

  const data = await backendResponse.json();
  return NextResponse.json(data, { status: backendResponse.status });
}
