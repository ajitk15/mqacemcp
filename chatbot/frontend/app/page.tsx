import ChatPane from "@/components/chat/ChatPane";
import { getBackendInfo } from "@/lib/backend-info";

export const dynamic = "force-dynamic";

export default async function Home() {
  const info = await getBackendInfo();

  // Subtitle precedence: explicit HEADER_SUBTITLE override -> auto-derived
  // from scope -> generic states.
  const autoSubtitle = !info.reachable
    ? "backend unreachable"
    : info.botDomain
    ? `scope: ${info.botDomain}`
    : "connected to MCP backend";
  const subtitle = info.headerSubtitle ?? autoSubtitle;

  return (
    <main className="mx-auto flex h-screen max-w-4xl flex-col bg-bg">
      <header className="flex items-center justify-between border-b border-border px-6 py-3">
        <h1 className="text-lg font-semibold text-fg">{info.headerTitle}</h1>
        <span className="text-xs text-muted">{subtitle}</span>
      </header>
      <ChatPane info={info} />
    </main>
  );
}
