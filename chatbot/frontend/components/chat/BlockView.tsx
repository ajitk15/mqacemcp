"use client";

import type { Block } from "@/lib/types";
import TableBlock from "./TableBlock";
import CodeBlock from "./CodeBlock";
import MermaidBlock from "./MermaidBlock";
import Markdown from "./Markdown";

export default function BlockView({ block }: { block: Block }) {
  switch (block.kind) {
    case "table":
      return <TableBlock columns={block.columns} rows={block.rows} title={block.title} />;
    case "code":
      return <CodeBlock code={block.code} lang={block.lang} title={block.title} />;
    case "mermaid":
      return <MermaidBlock source={block.mermaid} title={block.title} />;
    case "markdown":
      return <Markdown text={block.text} />;
    case "text":
      return <p className="my-1 whitespace-pre-wrap text-sm">{block.text}</p>;
  }
}
