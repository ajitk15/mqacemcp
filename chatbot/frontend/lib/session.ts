const KEY = "mcp-chatbot-thread-id";

function newUUID(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for insecure contexts (e.g. http://<lan-ip>:3000) where
  // crypto.randomUUID is not exposed. RFC4122 v4 shape, not cryptographically strong.
  const bytes = new Uint8Array(16);
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const h = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return `${h.slice(0, 4).join("")}-${h.slice(4, 6).join("")}-${h.slice(6, 8).join("")}-${h.slice(8, 10).join("")}-${h.slice(10, 16).join("")}`;
}

export function getOrCreateThreadId(): string {
  if (typeof window === "undefined") return "";
  let tid = window.localStorage.getItem(KEY);
  if (!tid) {
    tid = newUUID();
    window.localStorage.setItem(KEY, tid);
  }
  return tid;
}

export function rotateThreadId(): string {
  if (typeof window === "undefined") return "";
  const tid = newUUID();
  window.localStorage.setItem(KEY, tid);
  return tid;
}
