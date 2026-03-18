import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = "http://95.179.243.31:8000/api/v1";
const API_SECRET = process.env.NEXT_PUBLIC_API_SECRET ?? "";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, (await params).path);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, (await params).path);
}

export async function PUT(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, (await params).path);
}

export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  return proxyRequest(request, (await params).path);
}

async function proxyRequest(request: NextRequest, pathSegments: string[]) {
  const path = pathSegments.join("/");
  const searchParams = request.nextUrl.searchParams.toString();
  const url = `${BACKEND_URL}/${path}${searchParams ? `?${searchParams}` : ""}`;

  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : await request.text();

  try {
    const response = await fetch(url, {
      method: request.method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${API_SECRET}`,
      },
      body,
    });

    const data = await response.json();
    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error(`Proxy error for ${url}:`, error);
    return NextResponse.json(
      { error: "Failed to connect to backend", detail: String(error) },
      { status: 502 }
    );
  }
}
