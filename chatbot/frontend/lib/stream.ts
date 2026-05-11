import type { ChatEvent } from "./types";

/**
 * POST a chat message to /api/chat and dispatch decoded SSE events.
 * Generic — knows nothing about which MCP server is on the other side.
 */
export async function streamChat(
  message: string,
  threadId: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message, thread_id: threadId }),
    signal,
  });
  if (!res.ok || !res.body) {
    onEvent({ kind: "error", message: `HTTP ${res.status}` });
    onEvent({ kind: "done" });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    // Each SSE event is separated by a blank line.
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const dataLine = raw
        .split("\n")
        .find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        onEvent(JSON.parse(json) as ChatEvent);
      } catch {
        // ignore malformed event
      }
    }
  }
}

export async function resetThread(threadId: string): Promise<void> {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ thread_id: threadId }),
  });
}
