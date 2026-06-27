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
import ssl
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

log = logging.getLogger("chatbot.mcp")


def _build_headers(
    auth_user: str | None = None,
    auth_pwd: str | None = None,
    headers_json: str | None = None,
) -> dict[str, str]:
    """Build request headers. Each arg falls back to its env var when None."""
    headers: dict[str, str] = {}

    user = (auth_user if auth_user is not None else os.getenv("MCP_AUTH_USER", "")).strip()
    pwd = (auth_pwd if auth_pwd is not None else os.getenv("MCP_AUTH_PASSWORD", "")).strip()
    if user and pwd:
        token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    extra_raw = (headers_json if headers_json is not None else os.getenv("MCP_HEADERS_JSON", "")).strip()
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


def _resolve_ca_bundle() -> Path | None:
    """Return the resolved path to MCP_TLS_CA_BUNDLE, or None if unset."""
    raw = os.getenv("MCP_TLS_CA_BUNDLE", "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        # Resolve relative to repo root (backend/mcp_client.py -> ../)
        p = (Path(__file__).resolve().parent.parent / p).resolve()
    if not p.is_file():
        raise RuntimeError(f"MCP_TLS_CA_BUNDLE={raw} not found at {p}")
    return p


def _tls_insecure() -> bool:
    """True when MCP TLS verification should be skipped (self-signed endpoints)."""
    return os.getenv("MCP_TLS_INSECURE", "").strip().lower() in {"1", "true", "yes", "on"}


def _make_factory(verify: Any):
    """Return an httpx client factory using ``verify`` for TLS.

    ``verify`` is whatever httpx accepts: an ssl.SSLContext (pinned CA),
    a path string, or False to disable verification entirely.
    """

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"follow_redirects": True, "verify": verify}
        kwargs["timeout"] = timeout if timeout is not None else httpx.Timeout(
            MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT
        )
        if headers:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return factory


def _make_pinned_factory(ca_path: Path):
    """Return an httpx client factory that trusts only ``ca_path`` for TLS."""
    return _make_factory(ssl.create_default_context(cafile=str(ca_path)))


def build_client(
    url: str | None = None,
    auth_user: str | None = None,
    auth_pwd: str | None = None,
    headers_json: str | None = None,
) -> MultiServerMCPClient:
    """Construct a MultiServerMCPClient pointed at an SSE URL.

    ``url`` defaults to MCP_SSE_URL; auth args default to their env vars. This
    lets the backend switch the active MCP server at runtime without restart.
    """
    url = (url or os.getenv("MCP_SSE_URL", "")).strip()
    if not url:
        raise RuntimeError("MCP_SSE_URL is not set. See .env.example.")

    server_cfg: dict[str, Any] = {
        "url": url,
        "transport": "sse",
        "headers": _build_headers(auth_user, auth_pwd, headers_json),
    }
    if _tls_insecure():
        log.warning(
            "MCP_TLS_INSECURE is set — skipping TLS verification for %s. "
            "Only use this for self-signed/internal endpoints.",
            url,
        )
        server_cfg["httpx_client_factory"] = _make_factory(False)
    else:
        ca_path = _resolve_ca_bundle()
        if ca_path is not None:
            log.info("Pinning MCP TLS to CA bundle: %s", ca_path)
            server_cfg["httpx_client_factory"] = _make_pinned_factory(ca_path)

    log.info("Connecting to MCP server at %s", url)
    return MultiServerMCPClient({_server_name(): server_cfg})


async def load_tools(
    url: str | None = None,
    auth_user: str | None = None,
    auth_pwd: str | None = None,
    headers_json: str | None = None,
) -> list[BaseTool]:
    """Fetch MCP tools from ``url`` (default MCP_SSE_URL), then apply filtering."""
    client = build_client(url, auth_user, auth_pwd, headers_json)
    raw = await client.get_tools()
    log.info("Discovered %d MCP tools: %s", len(raw), [t.name for t in raw])
    tools = _filter_tools(raw)
    log.info("Exposing %d tool(s) to the agent: %s", len(tools), [t.name for t in tools])
    return tools
