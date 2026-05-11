import { NextRequest } from "next/server";

// Proxies the browser's chat request to the FastAPI backend so the
// backend URL and any future auth never appear in the client bundle.

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8001";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const upstream = await fetch(`${BACKEND}/api/chat/stream`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body,
    cache: "no-store",
  });

  if (!upstream.ok || !upstream.body) {
    return new Response(`Backend error: ${upstream.status}`, { status: 502 });
  }

  return new Response(upstream.body, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      "x-accel-buffering": "no",
    },
  });
}
