"""Composites-only MQ + ACE MCP server for single-tool-call orchestrators.

Same operational posture as the granular `mqacemcpserver.py` (stdio or SSE,
optional Basic Auth, optional TLS, `/healthz` always open), but the tool
catalogue is six composite tools — each one self-sufficient — so a frontend
that can only call one tool per user turn can still answer the common MQ
and ACE diagnostic intents end-to-end.

Selected by MCP_TRANSPORT in `.env`:
  - streamable-http: Streamable HTTP endpoint at http://MCP_HOST:MCP_PORT/mcp (default)
  - sse:             legacy HTTP/SSE endpoint at http://MCP_HOST:MCP_PORT/sse (deprecated)
  - stdio:           standard MCP stdio transport (local/dev)
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

logger = get_logger("mqacemcpserver")

# ---------------------------------------------------------------------------
# Build the MCP server and register only the composite tools
# ---------------------------------------------------------------------------
mcp = FastMCP("mqacemcpserver", host=MCP_HOST, port=MCP_PORT)

composite_tools.register(mcp)


async def _shutdown() -> None:
    """Close shared HTTP clients. Best-effort; never raises."""
    await asyncio.gather(
        mq_helpers.aclose_http_client(),
        ace_helpers.aclose_http_client(),
        return_exceptions=True,
    )


# ---------------------------------------------------------------------------
# /healthz — unauthenticated liveness/readiness probe (HTTP transports only)
# ---------------------------------------------------------------------------
def _healthz_payload() -> dict:
    return {
        "status": "ok",
        "service": "mqacemcpserver",
        "transport": MCP_TRANSPORT,
        "mq_configured": mq_configured(),
        "ace_configured": ace_configured(),
        "manifests": manifest_status(),
    }


async def _healthz_app(scope, receive, send) -> None:
    body = json.dumps(_healthz_payload()).encode("utf-8")
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


def _build_http_app():
    """Compose the HTTP app for the active transport, add an unauthenticated
    /healthz, then apply optional Basic Auth.

    Streamable HTTP needs the Starlette app's lifespan to run (it starts the
    session manager), so /healthz is mounted INTO that app rather than wrapped
    in a bare router (which would drop the lifespan). SSE needs no lifespan, so
    the lightweight router is fine there.
    """
    if MCP_TRANSPORT == "streamable-http":
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def _healthz_route(_request):
            return JSONResponse(_healthz_payload(), headers={"cache-control": "no-store"})

        app = mcp.streamable_http_app()
        # Exact-match Route (not Mount — Mount 307-redirects /healthz -> /healthz/).
        # Insert first so it wins over the /mcp routes; preserves the app lifespan.
        app.router.routes.insert(0, Route("/healthz", _healthz_route, methods=["GET"]))
    else:  # sse (legacy)
        sse_app = mcp.sse_app()

        async def app(scope, receive, send):
            if scope.get("type") == "http" and scope.get("path") == "/healthz":
                await _healthz_app(scope, receive, send)
                return
            await sse_app(scope, receive, send)

    if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
        return BasicAuthMiddleware(app, MCP_AUTH_USER, MCP_AUTH_PASSWORD)
    return app


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
    _http = MCP_TRANSPORT in ("streamable-http", "sse")
    if _http:
        scheme = "https" if tls_enabled() else "http"
        path = "/mcp" if MCP_TRANSPORT == "streamable-http" else "/sse"
        logger.info("MCP %s endpoint: %s://%s:%s%s", MCP_TRANSPORT, scheme, MCP_HOST, MCP_PORT, path)
        logger.info("Health check: %s://%s:%s/healthz", scheme, MCP_HOST, MCP_PORT)

    try:
        if _http:
            import uvicorn

            app = _build_http_app()
            if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
                logger.info(
                    "MCP endpoint protected by HTTP Basic Auth (user=%s)",
                    MCP_AUTH_USER,
                )
            else:
                logger.warning(
                    "MCP endpoint is UNAUTHENTICATED. Set MCP_AUTH_USER and "
                    "MCP_AUTH_PASSWORD in .env to enable Basic Auth."
                )
            uvicorn_kwargs: dict = {"host": MCP_HOST, "port": MCP_PORT}
            if tls_enabled():
                uvicorn_kwargs["ssl_certfile"] = MCP_TLS_CERT
                uvicorn_kwargs["ssl_keyfile"] = MCP_TLS_KEY
                logger.info(
                    "MCP endpoint TLS enabled (cert=%s, key=%s)",
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
