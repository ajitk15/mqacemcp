"use client";

import { useState } from "react";
import { ChevronRight, ChevronDown, Wrench } from "lucide-react";
import type { ToolStepRecord } from "@/lib/types";
import BlockView from "./BlockView";

export default function ToolStep({ step }: { step: ToolStepRecord }) {
  const [open, setOpen] = useState(true);
  const hasArgs = Object.keys(step.args).length > 0;

  return (
    <div className="my-2 rounded-md border border-border bg-panel">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-muted hover:text-accent"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Wrench size={12} />
        <span className="font-mono text-accent">{step.name}</span>
        {hasArgs && (
          <span className="font-mono text-muted">
            ({Object.entries(step.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
          </span>
        )}
      </button>
      {open && step.result && (
        <div className="border-t border-border p-2">
          <BlockView block={step.result} />
        </div>
      )}
      {open && !step.result && (
        <div className="border-t border-border px-3 py-2 text-xs text-muted">
          running…
        </div>
      )}
    </div>
  );
}
