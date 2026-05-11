const KEY = "mcp-chatbot-thread-id";

export function getOrCreateThreadId(): string {
  if (typeof window === "undefined") return "";
  let tid = window.localStorage.getItem(KEY);
  if (!tid) {
    tid = crypto.randomUUID();
    window.localStorage.setItem(KEY, tid);
  }
  return tid;
}

export function rotateThreadId(): string {
  if (typeof window === "undefined") return "";
  const tid = crypto.randomUUID();
  window.localStorage.setItem(KEY, tid);
  return tid;
}
