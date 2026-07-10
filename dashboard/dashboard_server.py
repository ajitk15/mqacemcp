#!/usr/bin/env python
"""Standalone HTTP server that exposes the MQ+ACE log insights dashboard.

This runs in its own process and is fully self-contained: it depends only on
the standard library, ``uvicorn``, ``python-dotenv`` and its sibling
``analyze_logs.py`` — NOT on the MCP server's ``server`` package. It loads its
own ``dashboard/.env`` and renders fresh HTML on every request.

Endpoints
---------
  GET /dashboard            — tabbed wrapper, one tab per configured MCP server
  GET /dashboard/<key>      — full HTML dashboard for that server's log dir
  GET /dashboard/questions  — static MQ/ACE/cert question-bank page
  GET /healthz              — liveness probe (lists every server's log dir)

Configuration (dashboard/.env, or process env which takes precedence)
---------------------------------------------------------------------
  MCP_DASHBOARD_HOST          default 0.0.0.0
  MCP_DASHBOARD_PORT          default 8002
  MCP_DASHBOARD_SERVERS_JSON  JSON array of {"name","key","log_dir"} — one tab
                              per entry. Unset -> single tab from LOG_DIR.
  LOG_DIR                     log directory for the fallback single tab
  MCP_TLS_CERT / MCP_TLS_KEY  set BOTH to serve HTTPS; unset -> HTTP

The endpoint has no authentication by design; do not bind to a publicly
reachable interface unless that is acceptable in your environment.

Run
---
  dashboard\\.venv\\Scripts\\python.exe dashboard\\dashboard_server.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# Self-contained: only `analyze_logs` (beside this file) is imported, so just
# this directory goes on sys.path. Load our OWN dashboard/.env (process env
# still wins, which load_dotenv honours by default).
_DASHBOARD_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DASHBOARD_DIR.parent
if str(_DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_DASHBOARD_DIR / ".env")

import uvicorn  # noqa: E402

import analyze_logs  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("mqacemcpserver.dashboard")

DASHBOARD_HOST: str = os.getenv("MCP_DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT: int = int(os.getenv("MCP_DASHBOARD_PORT", "8002"))


def _resolve_path(value: str) -> str:
    """Expand ~ / $VARS and anchor a relative path under the dashboard dir."""
    if not value or not value.strip():
        return ""
    p = Path(os.path.expandvars(os.path.expanduser(value.strip())))
    return str(p if p.is_absolute() else (_DASHBOARD_DIR / p).resolve())


# TLS is optional: serve HTTPS only when BOTH cert and key are provided.
MCP_TLS_CERT: str = _resolve_path(os.getenv("MCP_TLS_CERT", ""))
MCP_TLS_KEY: str = _resolve_path(os.getenv("MCP_TLS_KEY", ""))


def _tls_enabled() -> bool:
    return bool(MCP_TLS_CERT and MCP_TLS_KEY)


# Fallback single-tab log dir. Resolved the SAME way as the TLS paths above:
# a relative LOG_DIR is anchored under dashboard/ (NOT the current working
# directory), so it's deterministic no matter how the process is launched.
# Default when unset: dashboard/logs.
_log_dir_env = os.getenv("LOG_DIR", "").strip()
LOG_DIR: Path = Path(_resolve_path(_log_dir_env)) if _log_dir_env else (_DASHBOARD_DIR / "logs")


def _resolve_log_dir(raw: str) -> Path:
    """Resolve a log_dir from the registry; relative paths hang off the repo root."""
    p = Path(raw)
    return p.resolve() if p.is_absolute() else (_REPO_ROOT / p).resolve()


def _servers() -> list[dict]:
    """Per-server tab config: list of {name, key, log_dir(Path)}.

    Parsed from MCP_DASHBOARD_SERVERS_JSON; falls back to a single tab reading
    the local LOG_DIR (the legacy single-server behaviour).
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
    # Fixed extra tab: static question-bank page (served from /dashboard/questions).
    buttons += '<button class="tab questions" data-key="questions" onclick="pick(this)">&#10067; Questions</button>'
    first_key = escape(servers[0]["key"]) if servers else "default"
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MQ + ACE — Log Insights</title>
<style>
  :root {{ color-scheme: light; }}
  html, body {{ margin: 0; height: 100%; background: #F7F3FC;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }}
  /* Brand purple nav — mirrors the chat UI's fixed top bar. */
  .topbar {{ background: linear-gradient(90deg, #A100FF 0%, #7500C0 100%);
    padding: 8px 16px 0; box-shadow: 0 1px 5px rgba(0,0,0,0.12); }}
  .brandline {{ color: #ffffff; font-size: 13px; font-weight: 600;
    letter-spacing: .2px; padding: 2px 4px 8px; }}
  .tabs {{ display: flex; gap: 6px; }}
  .tab {{ background: rgba(255,255,255,0.15); color: #ffffff;
    border: 1px solid rgba(255,255,255,0.28); border-bottom: none;
    border-radius: 8px 8px 0 0; padding: 8px 16px; font-size: 0.9rem; cursor: pointer; }}
  .tab:hover {{ background: rgba(255,255,255,0.28); }}
  .tab.active {{ background: #F7F3FC; color: #6b21a8; border-color: transparent;
    font-weight: 700; }}
  .tab.questions {{ margin-left: auto; }}
  iframe {{ border: 0; width: 100%; height: calc(100vh - 70px); display: block; background: #ffffff; }}
</style></head><body>
  <div class="topbar">
    <div class="brandline">MQ + ACE &mdash; Log Insights</div>
    <div class="tabs">{buttons}</div>
  </div>
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


def _questions_path() -> Path:
    """Path to the static question-bank HTML page (lives beside this script)."""
    return _DASHBOARD_DIR / "mq_ace_cert_questions.html"


async def _serve_questions(send) -> None:
    path = _questions_path()
    try:
        body = path.read_bytes()
        status = 200
    except Exception:
        logger.exception("Failed to read questions page %s", path)
        body = (
            b"<!DOCTYPE html><html><body style=\"font-family:sans-serif;padding:2em;\">"
            b"<h1>Questions page not found</h1>"
            b"<p>Expected mq_ace_cert_questions.html in the dashboard folder.</p>"
            b"</body></html>"
        )
        status = 404
    await _send_response(send, status, b"text/html; charset=utf-8", body)


async def _serve_healthz(send) -> None:
    payload = {
        "status": "ok",
        "service": "mqacemcpserver-dashboard",
        "servers": [{"key": s["key"], "name": s["name"], "log_dir": str(s["log_dir"])} for s in _servers()],
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
    elif path == "/dashboard/questions":
        await _serve_questions(send)
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
    scheme = "https" if _tls_enabled() else "http"
    logger.info(
        "Starting dashboard server on %s://%s:%s/dashboard",
        scheme, DASHBOARD_HOST, DASHBOARD_PORT,
    )
    for s in _servers():
        logger.info("Tab %r (%s) reads logs from: %s", s["name"], s["key"], s["log_dir"])
    uvicorn_kwargs: dict = {"host": DASHBOARD_HOST, "port": DASHBOARD_PORT}
    if _tls_enabled():
        uvicorn_kwargs["ssl_certfile"] = MCP_TLS_CERT
        uvicorn_kwargs["ssl_keyfile"] = MCP_TLS_KEY
        logger.info("TLS enabled (cert=%s, key=%s)", MCP_TLS_CERT, MCP_TLS_KEY)
    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()