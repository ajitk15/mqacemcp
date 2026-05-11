import "server-only";
import type { BackendInfo } from "./types";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8001";

const FALLBACK: BackendInfo = {
  botDomain: null,
  toolCount: 0,
  reachable: false,
  headerTitle: "MCP Chatbot",
  headerSubtitle: null,
};

/**
 * Fetched server-side at page render so the frontend stays generic — it
 * just renders whatever the backend reports. If the backend is down we
 * return safe defaults rather than throwing, so the page still loads.
 */
export async function getBackendInfo(): Promise<BackendInfo> {
  try {
    const res = await fetch(`${BACKEND}/api/health`, { cache: "no-store" });
    if (!res.ok) return FALLBACK;
    const data = (await res.json()) as {
      bot_domain?: string;
      tool_count?: number;
      header_title?: string;
      header_subtitle?: string;
    };
    const domain = (data.bot_domain ?? "").trim();
    const subtitle = (data.header_subtitle ?? "").trim();
    return {
      botDomain: domain.length ? domain : null,
      toolCount: data.tool_count ?? 0,
      reachable: true,
      headerTitle: (data.header_title ?? "").trim() || "MCP Chatbot",
      headerSubtitle: subtitle.length ? subtitle : null,
    };
  } catch {
    return FALLBACK;
  }
}
