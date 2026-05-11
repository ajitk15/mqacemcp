"use client";

import { useEffect, useRef, useState } from "react";

interface Props {
  source: string;
  title?: string;
}

let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => {
      m.default.initialize({
        startOnLoad: false,
        theme: "default",
        securityLevel: "strict",
      });
      return m.default;
    });
  }
  return mermaidPromise;
}

export default function MermaidBlock({ source, title }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    (async () => {
      try {
        const mermaid = await loadMermaid();
        const id = `mmd-${Math.random().toString(36).slice(2)}`;
        const { svg } = await mermaid.render(id, source);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source]);

  return (
    <div className="my-2 rounded-md border border-border bg-panel">
      <div className="border-b border-border px-3 py-1 text-xs uppercase tracking-wide text-muted">
        {title ?? "diagram"}
      </div>
      {error ? (
        <div className="p-3 text-xs text-red-600">
          Diagram failed: {error}
          <pre className="mt-2 whitespace-pre-wrap text-muted">{source}</pre>
        </div>
      ) : (
        <div ref={ref} className="overflow-x-auto bg-white p-3" />
      )}
    </div>
  );
}
