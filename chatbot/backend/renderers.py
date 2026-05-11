"""Generic, tool-name-agnostic rendering of MCP tool output.

The backend never hardcodes which MCP server it's talking to. Instead this
module inspects each tool's raw return value and infers the best UI block:

  1. JSON object containing a list under a common key   -> table
  2. JSON list of objects                                -> table
  3. JSON object only                                    -> key/value table
  4. Plain text of repeated "k:v k:v ..." lines          -> table
  5. Text containing a ```mermaid fence                  -> mermaid
  6. Anything else multi-line                            -> code (monospace)
  7. Single line                                         -> text

Add a new heuristic here, not a new per-tool function — that keeps the
backend portable across MCP servers.
"""
from __future__ import annotations

import json
import re
from typing import Any

from schemas import Block

# Keys that commonly hold the "main payload list" in JSON envelopes.
# Kept generic on purpose — not tied to MQ/ACE field names.
_LIST_KEYS = (
    "results",
    "items",
    "data",
    "records",
    "rows",
    "entries",
    "children",
    "nodes",
    "servers",
    "applications",
    "flows",
    "configured_nodes",
)

# Matches "key: value" or "key:value" pairs where the value has no whitespace.
_KV_PAIR_RE = re.compile(r"([A-Za-z_][\w.-]*)\s*:\s*(\S+)")

# Matches a ```mermaid ... ``` fence in free-form text.
_MERMAID_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)


def render(tool_name: str, raw: Any) -> Block:
    """Produce a UI Block for a tool result.

    `tool_name` is accepted only so it can be included in the block title;
    no branching on its value happens here, by design.
    """
    text = raw if isinstance(raw, str) else _stringify(raw)
    title = tool_name

    # 1. JSON path
    parsed = _try_json(text)
    if parsed is not None:
        block = _from_json(parsed)
        if block is not None:
            block.title = title
            return block

    # 2. Embedded mermaid fence
    mermaid_match = _MERMAID_FENCE_RE.search(text)
    if mermaid_match:
        return Block(kind="mermaid", mermaid=mermaid_match.group(1).strip(), title=title)

    # 3. Repeated "k:v k:v" lines → table
    kv_table = _try_keyvalue_lines(text)
    if kv_table is not None:
        kv_table.title = title
        return kv_table

    # 4. Multi-line → code block; single line → plain text
    if "\n" in text.strip():
        return Block(kind="code", code=text, lang="text", title=title)
    return Block(kind="text", text=text, title=title)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, default=str)
    except Exception:
        return str(value)


def _try_json(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _from_json(data: Any) -> Block | None:
    # List of dicts
    if isinstance(data, list):
        if data and all(isinstance(r, dict) for r in data):
            return _rows_to_table(data)
        return Block(kind="code", code=json.dumps(data, indent=2), lang="json")

    if isinstance(data, dict):
        # Find a "main list" key
        for key in _LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                return _rows_to_table(value)

        # Nested raw_response.children
        raw = data.get("raw_response")
        if isinstance(raw, dict):
            for key in _LIST_KEYS:
                value = raw.get(key)
                if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
                    return _rows_to_table(value)

        # Error envelopes: surface the message as plain text
        if data.get("status") == "error" and isinstance(data.get("message"), str):
            return Block(kind="text", text=str(data["message"]))

        # Otherwise key/value table over scalar fields
        return _dict_to_kv_table(data)

    return None


def _rows_to_table(rows: list[dict[str, Any]]) -> Block:
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if key in seen:
                continue
            if isinstance(value, (dict, list)):
                continue
            columns.append(key)
            seen.add(key)

    if not columns:
        # All values are nested structures — fall back to JSON code block.
        return Block(kind="code", code=json.dumps(rows, indent=2), lang="json")

    table_rows = [
        [_fmt(row.get(col)) for col in columns]
        for row in rows
    ]
    return Block(kind="table", columns=columns, rows=table_rows)


def _dict_to_kv_table(data: dict[str, Any]) -> Block:
    rows: list[list[str]] = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            rows.append([str(key), json.dumps(value, default=str)[:200]])
        else:
            rows.append([str(key), _fmt(value)])
    if not rows:
        return Block(kind="text", text=json.dumps(data, indent=2))
    return Block(kind="table", columns=["field", "value"], rows=rows)


def _try_keyvalue_lines(text: str) -> Block | None:
    columns: list[str] | None = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pairs = _KV_PAIR_RE.findall(line)
        if len(pairs) < 2:
            return None
        keys = [k for k, _ in pairs]
        values = [v for _, v in pairs]
        if columns is None:
            columns = keys
        elif keys != columns:
            return None
        rows.append(values)
    if columns and len(rows) >= 2:
        return Block(kind="table", columns=columns, rows=rows)
    return None


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
