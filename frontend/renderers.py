"""Block + tool-step renderers for the Streamlit chat UI.

These mirror the `Block` shapes in `chatbot/backend/schemas.py`. They are
MCP-server-agnostic — no tool names are referenced.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components


_MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


_MERMAID_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  body {{
    margin: 0;
    padding: 12px;
    background: #ffffff;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }}
  .mermaid {{ background: #ffffff; }}
  .error {{ color: #b91c1c; font-size: 12px; }}
  pre {{ background: #f1f5f9; padding: 8px; border-radius: 6px; font-size: 12px; }}
</style>
</head>
<body>
  <div class="mermaid">{source}</div>
  <script>
    try {{
      mermaid.initialize({{ startOnLoad: true, theme: "default", securityLevel: "strict" }});
    }} catch (e) {{
      document.body.innerHTML =
        '<div class="error">Diagram failed: ' + e.message + '</div>' +
        '<pre>' + {raw_json} + '</pre>';
    }}
  </script>
</body>
</html>
"""


def render_mermaid(source: str, height: int = 420) -> None:
    """Render a mermaid diagram inside an isolated iframe (CDN-loaded)."""
    if not source or not source.strip():
        return
    raw_json = json.dumps(source)
    html_doc = _MERMAID_TEMPLATE.format(source=source, raw_json=raw_json)
    components.html(html_doc, height=height, scrolling=True)


def render_markdown(text: str) -> None:
    """Render markdown, lifting fenced ```mermaid``` blocks into real diagrams."""
    if not text:
        return
    parts = _MERMAID_RE.split(text)
    # _MERMAID_RE.split returns [md, diagram, md, diagram, ...] when matches exist.
    for index, part in enumerate(parts):
        if index % 2 == 0:
            stripped = part.strip()
            if stripped:
                st.markdown(part)
        else:
            render_mermaid(part)


def render_block(block: Dict[str, Any]) -> None:
    """Render a single Block by kind (text / markdown / code / mermaid / table)."""
    if not isinstance(block, dict):
        st.markdown(str(block))
        return

    kind = block.get("kind", "text")
    title = block.get("title")

    if title:
        st.caption(title)

    if kind == "text":
        text = block.get("text") or ""
        if text:
            st.markdown(_as_codefenced_if_multiline(text))

    elif kind == "markdown":
        render_markdown(block.get("text") or "")

    elif kind == "code":
        lang = block.get("lang") or None
        st.code(block.get("code") or "", language=lang)

    elif kind == "mermaid":
        render_mermaid(block.get("mermaid") or "")

    elif kind == "table":
        columns = block.get("columns") or []
        rows = block.get("rows") or []
        if columns:
            data = [
                {col: (row[col_index] if col_index < len(row) else "") for col_index, col in enumerate(columns)}
                for row in rows
            ]
            st.dataframe(data, use_container_width=True, hide_index=True)
        else:
            for row in rows:
                st.markdown(" | ".join(str(cell) for cell in row))

    else:
        # Unknown kind — fall back to a JSON dump so the user can still see it.
        st.code(json.dumps(block, indent=2, default=str), language="json")


def _as_codefenced_if_multiline(text: str) -> str:
    """Preserve whitespace for multi-line plain text via a fenced block."""
    if "\n" in text.strip():
        return f"```\n{text}\n```"
    return text


def render_tool_step(step: Dict[str, Any], running: bool = False) -> None:
    """Render a tool invocation: name + args header, expandable result panel."""
    name = step.get("name") or "tool"
    args = step.get("args") or {}
    result = step.get("result")

    args_summary = ""
    if isinstance(args, dict) and args:
        try:
            args_summary = ", ".join(f"{k}={json.dumps(v, default=str)}" for k, v in args.items())
        except Exception:
            args_summary = str(args)
        if len(args_summary) > 140:
            args_summary = args_summary[:137] + "…"

    icon = "⏳" if running and result is None else "🔧"
    label = f"{icon}  {name}"
    if args_summary:
        label = f"{label}  ({args_summary})"

    with st.expander(label, expanded=True):
        if result is not None:
            render_block(result)
        elif running:
            st.caption("running…")
        else:
            st.caption("no result")


def render_assistant_body(text: str, tool_steps: list, error: Optional[str] = None) -> None:
    """Render the full assistant turn body (tool steps first, then text, then error)."""
    for step in tool_steps:
        render_tool_step(step, running=step.get("result") is None)
    if text:
        render_markdown(text)
    if error:
        st.error(error)
