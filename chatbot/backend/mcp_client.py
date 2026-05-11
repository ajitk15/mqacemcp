"""MCP client wiring.

Connects to a single MCP server over SSE via `langchain-mcp-adapters`. Auth
is configured by env: Basic Auth (MCP_AUTH_USER/PASSWORD) and/or arbitrary
headers (MCP_HEADERS_JSON). All of this is generic — point MCP_SSE_URL at
any MCP server and the chatbot adapts.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

log = logging.getLogger("chatbot.mcp")


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {}

    user = os.getenv("MCP_AUTH_USER", "").strip()
    pwd = os.getenv("MCP_AUTH_PASSWORD", "").strip()
    if user and pwd:
        token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    extra_raw = os.getenv("MCP_HEADERS_JSON", "").strip()
    if extra_raw:
        try:
            extra: dict[str, Any] = json.loads(extra_raw)
            for k, v in extra.items():
                headers[str(k)] = str(v)
        except Exception:
            log.warning("MCP_HEADERS_JSON is not valid JSON; ignoring.")

    return headers


def _server_name() -> str:
    return os.getenv("MCP_SERVER_NAME", "mcp")


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_tool_filters() -> tuple[list[str], list[str]]:
    """Return (allowlist, denylist) from env. Both may be empty."""
    return (
        _split_csv(os.getenv("TOOL_ALLOWLIST", "")),
        _split_csv(os.getenv("TOOL_DENYLIST", "")),
    )


def _filter_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Apply env-driven allow/deny filtering. Deny wins for the same name."""
    allow, deny = get_tool_filters()
    if not allow and not deny:
        return tools

    available = {t.name for t in tools}
    for name in (set(allow) | set(deny)) - available:
        log.warning(
            "TOOL_ALLOWLIST/DENYLIST mentions unknown tool %r (available: %s)",
            name,
            sorted(available),
        )

    deny_set = set(deny)
    allow_set = set(allow)
    kept = [
        t for t in tools
        if t.name not in deny_set and (not allow_set or t.name in allow_set)
    ]
    dropped = [t.name for t in tools if t.name not in {k.name for k in kept}]
    if dropped:
        log.info(
            "Filtered out %d tool(s) via allow/deny: %s",
            len(dropped),
            dropped,
        )
    return kept


def build_client() -> MultiServerMCPClient:
    """Construct a MultiServerMCPClient pointed at the configured SSE URL."""
    url = os.getenv("MCP_SSE_URL", "").strip()
    if not url:
        raise RuntimeError("MCP_SSE_URL is not set. See .env.example.")

    config = {
        _server_name(): {
            "url": url,
            "transport": "sse",
            "headers": _build_headers(),
        }
    }
    log.info("Connecting to MCP server at %s", url)
    return MultiServerMCPClient(config)


async def load_tools() -> list[BaseTool]:
    """Fetch MCP tools, then apply env-driven allow/deny filtering."""
    client = build_client()
    raw = await client.get_tools()
    log.info("Discovered %d MCP tools: %s", len(raw), [t.name for t in raw])
    tools = _filter_tools(raw)
    log.info("Exposing %d tool(s) to the agent: %s", len(tools), [t.name for t in tools])
    return tools
