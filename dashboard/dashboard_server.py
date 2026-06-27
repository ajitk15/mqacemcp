#!/usr/bin/env python
"""Standalone HTTP server that exposes the MQ+ACE log insights dashboard.

This runs in its own process, completely independent of the MCP server. It
reads the same ``LOG_DIR`` from ``.env`` (via ``server.config``) and renders
fresh HTML on every request by reusing the functions in ``analyze_logs.py``.

Endpoints
---------
  GET /dashboard        — tabbed wrapper, one tab per configured MCP server
  GET /dashboard/<key>  — full HTML dashboard for that server's log dir
  GET /healthz          — liveness probe (lists every server's log dir)

Configuration (.env)
--------------------
  MCP_DASHBOARD_HOST          default 0.0.0.0
  MCP_DASHBOARD_PORT          default 8002
  MCP_DASHBOARD_SERVERS_JSON  JSON array of {"name","key","log_dir"} — one tab
                              per entry. Unset -> single tab from LOG_DIR.
  LOG_DIR                     shared with the MCP server (fallback single tab)

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


def _resolve_log_dir(raw: str) -> Path:
    """Resolve a log_dir from the registry; relative paths hang off the repo root."""
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (_REPO_ROOT / p).resolve()


def _servers() -> list[dict]:
    """Per-server tab config: list of {name, key, log_dir(Path)}.

    Parsed from MCP_DASHBOARD_SERVERS_JSON; falls back to a single tab reading
    the imported server.config LOG_DIR (the legacy single-server behaviour).
    """
    raw = os.getenv("MCP_DASHBOARD_SERVERS_JSON", "").strip()
    if raw:
        try:
            entries = json.loads(raw)
            out = []
            for i, e in enumerate(entries):
                if not isinstance(e, dict) or not e.get("log_dir"):
                    continue
                key = str(e.get("key") or f"s{i}")
                out.append(
                    {
                        "name": str(e.get("name") or key),
                        "key": key,
                        "log_dir": _resolve_log_dir(str(e["log_dir"])),
                    }
                )
            if out:
                return out
        except Exception:
            logger.exception("MCP_DASHBOARD_SERVERS_JSON is invalid; using LOG_DIR fallback.")
    return [{"name": "MCP server", "key": "default", "log_dir": Path(LOG_DIR)}]


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


def _build_tabs_page(servers: list[dict]) -> bytes:
    """A small wrapper page: one tab button per server + an iframe.

    Each tab loads /dashboard/<key> (the full per-server dashboard) in the
    iframe, so the heavy HTML from analyze_logs is reused unchanged.
    """
    from html import escape

    buttons = "".join(
        f'<button class="tab{" active" if i == 0 else ""}" '
        f'data-key="{escape(s["key"])}" onclick="pick(this)">{escape(s["name"])}</button>'
        for i, s in enumerate(servers)
    )
    # Fixed extra tab: head-to-head performance comparison (not a per-server log dir).
    buttons += '<button class="tab compare" data-key="compare" onclick="pick(this)">&#9878; Compare</button>'
    first_key = escape(servers[0]["key"]) if servers else "default"
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MQ + ACE — Log Insights</title>
<style>
  :root {{ color-scheme: dark; }}
  html, body {{ margin: 0; height: 100%; background: #0f172a;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  .tabs {{ display: flex; gap: 6px; padding: 10px 14px 0;
    background: #0f172a; border-bottom: 1px solid #1e293b; }}
  .tab {{ background: #1e293b; color: #cbd5e1; border: 1px solid #334155;
    border-bottom: none; border-radius: 8px 8px 0 0; padding: 8px 16px;
    font-size: 0.9rem; cursor: pointer; }}
  .tab:hover {{ background: #273449; }}
  .tab.active {{ background: #0b1220; color: #fff; border-color: #475569;
    font-weight: 600; }}
  .tab.compare {{ margin-left: auto; color: #6ee7b7; }}
  iframe {{ border: 0; width: 100%; height: calc(100vh - 49px); display: block; }}
</style></head><body>
  <div class="tabs">{buttons}</div>
  <iframe id="frame" src="dashboard/{first_key}" title="dashboard"></iframe>
  <script>
    function pick(btn) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('frame').src = 'dashboard/' + btn.dataset.key;
    }}
  </script>
</body></html>"""
    return page.encode("utf-8")


async def _serve_tabs(send) -> None:
    await _send_response(send, 200, b"text/html; charset=utf-8", _build_tabs_page(_servers()))


async def _serve_dashboard(send, log_dir: Path) -> None:
    try:
        html = analyze_logs.compute_dashboard_html(log_dir)
        body = html.encode("utf-8")
        status = 200
    except Exception:
        logger.exception("Failed to render dashboard for %s", log_dir)
        body = (
            b"<!DOCTYPE html><html><body style=\"font-family:sans-serif;padding:2em;\">"
            b"<h1>Dashboard error</h1><p>See server logs for details.</p>"
            b"</body></html>"
        )
        status = 500
    await _send_response(send, status, b"text/html; charset=utf-8", body)


def _compare_json_path() -> Path:
    """Path to the head-to-head benchmark results JSON (compare_servers.py output)."""
    raw = os.getenv("MCP_DASHBOARD_COMPARE_JSON", "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (_REPO_ROOT / p).resolve()
    return (_REPO_ROOT / "custom-logs" / "compare_results.json").resolve()


async def _serve_compare(send) -> None:
    try:
        html = analyze_logs.compute_comparison_html(_compare_json_path())
        body = html.encode("utf-8")
        status = 200
    except Exception:
        logger.exception("Failed to render comparison page")
        body = (
            b"<!DOCTYPE html><html><body style=\"font-family:sans-serif;padding:2em;\">"
            b"<h1>Comparison error</h1><p>See server logs for details.</p>"
            b"</body></html>"
        )
        status = 500
    await _send_response(send, status, b"text/html; charset=utf-8", body)


async def _serve_healthz(send) -> None:
    compare_path = _compare_json_path()
    payload = {
        "status": "ok",
        "service": "mqacemcpserver-dashboard",
        "servers": [{"key": s["key"], "name": s["name"], "log_dir": str(s["log_dir"])} for s in _servers()],
        "compare_json": str(compare_path),
        "compare_json_exists": compare_path.exists(),
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
        await _serve_tabs(send)
    elif path == "/dashboard/compare":
        await _serve_compare(send)
    elif path.startswith("/dashboard/"):
        key = path[len("/dashboard/"):].strip("/")
        match = next((s for s in _servers() if s["key"] == key), None)
        if match is None:
            await _serve_404(send)
        else:
            await _serve_dashboard(send, match["log_dir"])
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
    for s in _servers():
        logger.info("Tab %r (%s) reads logs from: %s", s["name"], s["key"], s["log_dir"])
    uvicorn_kwargs: dict = {"host": DASHBOARD_HOST, "port": DASHBOARD_PORT}
    if tls_enabled():
        uvicorn_kwargs["ssl_certfile"] = MCP_TLS_CERT
        uvicorn_kwargs["ssl_keyfile"] = MCP_TLS_KEY
        logger.info("TLS enabled (cert=%s, key=%s)", MCP_TLS_CERT, MCP_TLS_KEY)
    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()