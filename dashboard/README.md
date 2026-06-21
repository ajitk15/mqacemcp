# Dashboard component

A standalone HTTP server that renders the MQ + ACE **log-insights dashboard**
from the JSONL/text logs the MCP server writes. It runs in its own process and
its own venv, completely independent of the MCP server at runtime — it only
*reads* the same `LOG_DIR` and reuses the MCP server's `server.config` /
`server.logger` for configuration.

```
dashboard/
  dashboard_server.py   — ASGI app (uvicorn). GET /dashboard, GET /healthz
  analyze_logs.py       — pure-Python HTML/metrics builder (no third-party deps)
  requirements.txt      — uvicorn + python-dotenv
```

## How it finds the `server` package

`dashboard_server.py` imports `server.config` / `server.logger`, which live in
`../mqacemcpserver/`. The script adds that directory to `sys.path` at startup.
To point it at a different build (e.g. the single build), set:

```
MCP_SERVER_DIR=/path/to/mqacemcpserver-single
```

## One-time setup

```powershell
# Windows
cd dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# RHEL / Linux
cd dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```powershell
# Windows
.\.venv\Scripts\python.exe dashboard_server.py
```

```bash
# RHEL / Linux
./.venv/bin/python dashboard_server.py
```

Then open <http://localhost:8002/dashboard> (`MCP_DASHBOARD_PORT`, default 8002).

## Configuration (`.env` at repo root, shared with the MCP server)

| Var | Default | Purpose |
| --- | --- | --- |
| `MCP_DASHBOARD_HOST` | `0.0.0.0` | Bind host. |
| `MCP_DASHBOARD_PORT` | `8002` | Bind port. |
| `LOG_DIR` | `<repo>/logs` | Where the MCP server writes its logs. |
| `MCP_SERVER_DIR` | `../mqacemcpserver` | Which build's `server` package to import config from. |

The endpoint has **no authentication** by design — do not bind it to a publicly
reachable interface unless that is acceptable in your environment.
