"use client";

import { useEffect, useRef, useState } from "react";
import { Send, RotateCcw, Loader2 } from "lucide-react";
import type { BackendInfo, ChatEvent, ChatTurn, ToolStepRecord } from "@/lib/types";
import { getOrCreateThreadId, rotateThreadId } from "@/lib/session";
import { resetThread, streamChat } from "@/lib/stream";
import Message from "./Message";

interface Props {
  info: BackendInfo;
}

export default function ChatPane({ info }: Props) {
  const [threadId, setThreadId] = useState<string>("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setThreadId(getOrCreateThreadId());
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [turns]);

  const send = async () => {
    const text = input.trim();
    if (!text || !threadId || busy) return;
    setInput("");
    setBusy(true);

    const userTurn: ChatTurn = { role: "user", text, toolSteps: [], done: true };
    const assistantTurn: ChatTurn = { role: "assistant", text: "", toolSteps: [], done: false };
    setTurns((prev) => [...prev, userTurn, assistantTurn]);

    const updateAssistant = (mut: (t: ChatTurn) => ChatTurn) => {
      setTurns((prev) => {
        const next = prev.slice();
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = mut(last);
        }
        return next;
      });
    };

    const onEvent = (e: ChatEvent) => {
      switch (e.kind) {
        case "token":
          updateAssistant((t) => ({ ...t, text: t.text + e.text }));
          break;
        case "tool_call": {
          const step: ToolStepRecord = {
            name: e.name,
            args: e.args,
            callId: e.call_id,
          };
          updateAssistant((t) => ({ ...t, toolSteps: [...t.toolSteps, step] }));
          break;
        }
        case "tool_result":
          updateAssistant((t) => ({
            ...t,
            toolSteps: t.toolSteps.map((s) =>
              (e.call_id && s.callId === e.call_id) || (!e.call_id && s.name === e.name && !s.result)
                ? { ...s, result: e.block }
                : s,
            ),
          }));
          break;
        case "error":
          updateAssistant((t) => ({ ...t, error: e.message }));
          break;
        case "done":
          updateAssistant((t) => ({ ...t, done: true }));
          break;
      }
    };

    try {
      await streamChat(text, threadId, onEvent);
    } catch (err) {
      updateAssistant((t) => ({
        ...t,
        error: err instanceof Error ? err.message : String(err),
        done: true,
      }));
    } finally {
      setBusy(false);
    }
  };

  const onReset = async () => {
    if (threadId) await resetThread(threadId);
    setThreadId(rotateThreadId());
    setTurns([]);
  };

  return (
    <>
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {turns.length === 0 && (
          <div className="mt-12 text-center text-sm text-muted">
            {info.botDomain
              ? `Ask about ${info.botDomain}. The assistant will pick the right MCP tool and stream the answer back.`
              : "Ask anything. The assistant will pick the right MCP tool and stream the answer back."}
          </div>
        )}
        {turns.map((t, i) => (
          <Message key={i} turn={t} />
        ))}
      </div>

      <form
        className="flex items-center gap-2 border-t border-border px-6 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <button
          type="button"
          onClick={onReset}
          title="Start a new conversation"
          className="rounded-md p-2 text-muted hover:bg-panel hover:text-accent"
        >
          <RotateCcw size={16} />
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            info.botDomain
              ? `Ask about ${info.botDomain}…`
              : "Ask the MCP assistant…"
          }
          className="flex-1 rounded-md border border-border bg-panel px-3 py-2 text-sm outline-none focus:border-accent"
          disabled={busy}
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-md bg-accent/80 px-3 py-2 text-sm text-white hover:bg-accent disabled:opacity-40"
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
      </form>
    </>
  );
}
