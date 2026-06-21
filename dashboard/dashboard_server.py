#!/usr/bin/env python
"""Standalone HTTP server that exposes the MQ+ACE log insights dashboard.

This runs in its own process, completely independent of the MCP server. It
reads the same ``LOG_DIR`` from ``.env`` (via ``server.config``) and renders
fresh HTML on every request by reusing the functions in ``analyze_logs.py``.

Endpoints
---------
  GET /dashboard  — full HTML dashboard (regenerated per request)
  GET /healthz    — liveness probe

Configuration (.env)
--------------------
  MCP_DASHBOARD_HOST   default 0.0.0.0
  MCP_DASHBOARD_PORT   default 8002
  LOG_DIR              shared with the MCP server

The endpoint has no authentication by design; do not bind to a publicly
reachable interface unless that is acceptable in your environment.

Run
---
  dashboard\\.venv\\Scripts\\python.exe dashboard\\dashboard_server.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# This component lives in `dashboard/` but reuses the MCP server's config and
# logger. `analyze_logs` sits beside this file; the `server` package lives in
# `mqacemcpserver/`. Put both on the path. `MCP_SERVER_DIR` can override the MCP
# directory (e.g. to point at the single-build's `server` package instead).
_DASHBOARD_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DASHBOARD_DIR.parent
_MCP_DIR = Path(os.getenv("MCP_SERVER_DIR", str(_REPO_ROOT / "mqacemcpserver"))).resolve()
for _p in (_DASHBOARD_DIR, _MCP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import uvicorn  # noqa: E402

import analyze_logs  # noqa: E402
from server.config import LOG_DIR, MCP_TLS_CERT, MCP_TLS_KEY, tls_enabled  # noqa: E402
from server.logger import get_logger  # noqa: E402

logger = get_logger("mqacemcpserver.dashboard")

DASHBOARD_HOST: str = os.getenv("MCP_DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT: int = int(os.getenv("MCP_DASHBOARD_PORT", "8002"))


async def _send_response(send, status: int, content_type: bytes, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", content_type],
                [b"cache-control", b"no-store"],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _serve_dashboard(send) -> None:
    try:
        html = analyze_logs.compute_dashboard_html(LOG_DIR)
        body = html.encode("utf-8")
        status = 200
    except Exception:
        logger.exception("Failed to render /dashboard")
        body = (
            b"<!DOCTYPE html><html><body style=\"font-family:sans-serif;padding:2em;\">"
            b"<h1>Dashboard error</h1><p>See server logs for details.</p>"
            b"</body></html>"
        )
        status = 500
    await _send_response(send, status, b"text/html; charset=utf-8", body)


async def _serve_healthz(send) -> None:
    payload = {
        "status": "ok",
        "service": "mqacemcpserver-dashboard",
        "log_dir": str(LOG_DIR),
    }
    body = json.dumps(payload).encode("utf-8")
    await _send_response(send, 200, b"application/json", body)


async def _serve_404(send) -> None:
    await _send_response(send, 404, b"text/plain", b"Not Found")


async def app(scope, receive, send) -> None:
    if scope.get("type") != "http":
        return
    path = scope.get("path", "")
    if path in ("/dashboard", "/"):
        await _serve_dashboard(send)
    elif path == "/healthz":
        await _serve_healthz(send)
    else:
        await _serve_404(send)


def main() -> None:
    scheme = "https" if tls_enabled() else "http"
    logger.info(
        "Starting dashboard server on %s://%s:%s/dashboard",
        scheme, DASHBOARD_HOST, DASHBOARD_PORT,
    )
    logger.info("Reading logs from: %s", LOG_DIR)
    uvicorn_kwargs: dict = {"host": DASHBOARD_HOST, "port": DASHBOARD_PORT}
    if tls_enabled():
        uvicorn_kwargs["ssl_certfile"] = MCP_TLS_CERT
        uvicorn_kwargs["ssl_keyfile"] = MCP_TLS_KEY
        logger.info("TLS enabled (cert=%s, key=%s)", MCP_TLS_CERT, MCP_TLS_KEY)
    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()