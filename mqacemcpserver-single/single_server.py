"""Composites-only MQ + ACE MCP server for single-tool-call orchestrators.

Same operational posture as the granular `mqacemcpserver.py` (stdio or SSE,
optional Basic Auth, optional TLS, `/healthz` always open), but the tool
catalogue is six composite tools — each one self-sufficient — so a frontend
that can only call one tool per user turn can still answer the common MQ
and ACE diagnostic intents end-to-end.

Selected by MCP_TRANSPORT in `.env`:
  - stdio: standard MCP stdio transport (local/dev)
  - sse:   HTTP/SSE endpoint at http://MCP_HOST:MCP_PORT/sse
"""
from __future__ import annotations

import asyncio
import json

from mcp.server.fastmcp import FastMCP

from server import ace_helpers, composite_tools, mq_helpers, query_log
from server.auth import BasicAuthMiddleware
from server.csv_cache import all_status as manifest_status
from server.config import (
    LOG_DIR,
    MCP_AUTH_PASSWORD,
    MCP_AUTH_USER,
    MCP_HOST,
    MCP_PORT,
    MCP_TLS_CERT,
    MCP_TLS_KEY,
    MCP_TRANSPORT,
    QUERY_LOG_ENABLED,
    ace_configured,
    mq_configured,
    tls_enabled,
)
from server.logger import get_logger

logger = get_logger("mqacemcpserver-single")

# ---------------------------------------------------------------------------
# Build the MCP server and register only the composite tools
# ---------------------------------------------------------------------------
mcp = FastMCP("mqacemcpserver-single", host=MCP_HOST, port=MCP_PORT)

composite_tools.register(mcp)


async def _shutdown() -> None:
    """Close shared HTTP clients. Best-effort; never raises."""
    await asyncio.gather(
        mq_helpers.aclose_http_client(),
        ace_helpers.aclose_http_client(),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# /healthz — unauthenticated liveness/readiness probe (SSE only)
# ---------------------------------------------------------------------------
async def _healthz_app(scope, receive, send) -> None:
    payload = {
        "status": "ok",
        "service": "mqacemcpserver-single",
        "transport": MCP_TRANSPORT,
        "mq_configured": mq_configured(),
        "ace_configured": ace_configured(),
        "manifests": manifest_status(),
    }
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"application/json"],
                [b"cache-control", b"no-store"],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _build_sse_app():
    """Compose the SSE app: /healthz route + everything else → FastMCP, then optional Basic Auth."""
    sse_app = mcp.sse_app()

    async def router(scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/healthz":
            await _healthz_app(scope, receive, send)
            return
        await sse_app(scope, receive, send)

    if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
        return BasicAuthMiddleware(router, MCP_AUTH_USER, MCP_AUTH_PASSWORD)
    return router


def main() -> None:
    logger.info(
        "Starting composites-only MQ+ACE MCP server (transport=%s, host=%s, port=%s)",
        MCP_TRANSPORT,
        MCP_HOST,
        MCP_PORT,
    )
    logger.info(
        "Logs: dir=%s, query_log_enabled=%s", LOG_DIR, QUERY_LOG_ENABLED
    )
    if MCP_TRANSPORT == "sse":
        scheme = "https" if tls_enabled() else "http"
        logger.info("MCP SSE endpoint: %s://%s:%s/sse", scheme, MCP_HOST, MCP_PORT)
        logger.info("Health check: %s://%s:%s/healthz", scheme, MCP_HOST, MCP_PORT)

    try:
        if MCP_TRANSPORT == "sse":
            import uvicorn

            app = _build_sse_app()
            if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
                logger.info(
                    "SSE endpoint protected by HTTP Basic Auth (user=%s)",
                    MCP_AUTH_USER,
                )
            else:
                logger.warning(
                    "SSE endpoint is UNAUTHENTICATED. Set MCP_AUTH_USER and "
                    "MCP_AUTH_PASSWORD in .env to enable Basic Auth."
                )
            uvicorn_kwargs: dict = {"host": MCP_HOST, "port": MCP_PORT}
            if tls_enabled():
                uvicorn_kwargs["ssl_certfile"] = MCP_TLS_CERT
                uvicorn_kwargs["ssl_keyfile"] = MCP_TLS_KEY
                logger.info(
                    "SSE endpoint TLS enabled (cert=%s, key=%s)",
                    MCP_TLS_CERT, MCP_TLS_KEY,
                )
            uvicorn.run(app, **uvicorn_kwargs)
        else:
            mcp.run(transport=MCP_TRANSPORT)
    finally:
        try:
            asyncio.run(_shutdown())
        except Exception:
            logger.exception("Shutdown cleanup raised (continuing)")
        query_log.close()
        logger.info("Query log closed; server stopped.")


if __name__ == "__main__":
    main()
