export type Block =
  | { kind: "text"; text: string; title?: string }
  | { kind: "markdown"; text: string; title?: string }
  | { kind: "code"; code: string; lang?: string; title?: string }
  | { kind: "mermaid"; mermaid: string; title?: string }
  | {
      kind: "table";
      columns: string[];
      rows: string[][];
      title?: string;
    };

export type ChatEvent =
  | { kind: "token"; text: string }
  | { kind: "tool_call"; name: string; args: Record<string, unknown>; call_id?: string }
  | { kind: "tool_result"; name: string; call_id?: string; block: Block }
  | { kind: "final"; blocks: Block[] }
  | { kind: "error"; message: string }
  | { kind: "done" };

export interface ToolStepRecord {
  name: string;
  args: Record<string, unknown>;
  callId?: string;
  result?: Block;
}

export interface ChatTurn {
  role: "user" | "assistant";
  text: string;
  toolSteps: ToolStepRecord[];
  error?: string;
  done: boolean;
}

export interface BackendInfo {
  botDomain: string | null;
  toolCount: number;
  reachable: boolean;
  headerTitle: string;
  headerSubtitle: string | null;
}
