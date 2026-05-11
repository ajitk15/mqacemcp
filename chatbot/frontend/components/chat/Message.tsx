"use client";

import type { ChatTurn } from "@/lib/types";
import Markdown from "./Markdown";
import ToolStep from "./ToolStep";

export default function Message({ turn }: { turn: ChatTurn }) {
  const isUser = turn.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} my-3`}>
      <div
        className={`max-w-[90%] rounded-lg px-4 py-2 text-fg ${
          isUser
            ? "border border-accent/30 bg-accent/10"
            : "border border-border bg-panel"
        }`}
      >
        {isUser ? (
          <div className="whitespace-pre-wrap text-sm">{turn.text}</div>
        ) : (
          <>
            {turn.toolSteps.map((s, i) => (
              <ToolStep key={s.callId ?? `${s.name}-${i}`} step={s} />
            ))}
            {turn.text && <Markdown text={turn.text} />}
            {!turn.text && !turn.toolSteps.length && !turn.done && (
              <span className="text-xs text-muted">thinking…</span>
            )}
            {turn.error && (
              <div className="mt-2 text-xs text-red-600">{turn.error}</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
