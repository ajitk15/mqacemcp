"""Block + tool-step renderers for the Streamlit chat UI.

These mirror the `Block` shapes in `chatbot/agent/schemas.py`. They are
MCP-server-agnostic — no tool names are referenced.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import streamlit as st
import streamlit.components.v1 as components


_MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)

# Backstop for the agent occasionally wrapping a whole answer (or a table) in a
# language-less ``` fence, which renders as unstyled monospace and hides the
# table. `_FENCE_RE` captures a fence's language tag + body; `_TABLE_DELIM_RE`
# matches a Markdown table delimiter row (e.g. `| --- | --- |`).
_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_TABLE_DELIM_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", re.M)
# Only lift fences with no language (or a plain-text/markdown tag) — never real code.
_LIFTABLE_LANGS = {"", "text", "txt", "markdown", "md"}


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


def _lift_wrapped_tables(text: str) -> str:
    """Unwrap a language-less ``` fence whose body contains a Markdown table.

    The agent sometimes wraps a whole answer (or just a table) in a bare code
    fence, which renders as unstyled monospace and hides the table. When a
    fence has no (or a doc-ish) language tag AND its body has a Markdown table
    delimiter row, lift the body out so it renders as real Markdown. Genuine
    ```lang code blocks are left untouched.
    """
    def _maybe_lift(match: "re.Match[str]") -> str:
        lang = (match.group(1) or "").strip().lower()
        body = match.group(2)
        if lang in _LIFTABLE_LANGS and _TABLE_DELIM_RE.search(body):
            return body
        return match.group(0)

    return _FENCE_RE.sub(_maybe_lift, text)


def render_markdown(text: str) -> None:
    """Render markdown, lifting fenced ```mermaid``` blocks into real diagrams."""
    if not text:
        return
    parts = _MERMAID_RE.split(text)
    # _MERMAID_RE.split returns [md, diagram, md, diagram, ...] when matches exist.
    for index, part in enumerate(parts):
        if index % 2 == 0:
            part = _lift_wrapped_tables(part)
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
            st.dataframe(data, width='stretch', hide_index=True)
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

    with st.expander(label, expanded=False):  # collapsed by default — keep the answer front-and-center
        if result is not None:
            render_block(result)
        elif running:
            st.caption("running…")
        else:
            st.caption("no result")


def render_assistant_body(
    text: str,
    tool_steps: list,
    error: Optional[str] = None,
    show_tool_calls: bool = True,
) -> None:
    """Render the full assistant turn body (tool steps first, then text, then error).

    ``show_tool_calls`` gates the 🔧 tool-invocation panels; when False the
    steps are skipped (data is untouched — the caller still owns it).
    """
    if show_tool_calls:
        for step in tool_steps:
            render_tool_step(step, running=step.get("result") is None)
    if text:
        render_markdown(text)
    if error:
        st.error(error)
