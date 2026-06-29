# Dashboard component

A standalone HTTP server that renders the MQ + ACE **log-insights dashboard**
from the JSONL/text logs the MCP server writes. It runs in its own process and
its own venv, completely independent of the MCP server at runtime — it only
*reads* the same `LOG_DIR` and reuses the chosen build's `server.config` /
`server.logger` for `LOG_DIR` and TLS (bind host/port come from the process
environment; see [Configuration](#configuration)).

```
dashboard/
  dashboard_server.py   — ASGI app (uvicorn). GET /dashboard, GET /healthz
  analyze_logs.py       — pure-Python HTML/metrics builder (no third-party deps)
  requirements.txt      — uvicorn + python-dotenv
```

## How it finds the `server` package

`dashboard_server.py` imports `server.config` / `server.logger`, which live in
`../mqacemcpserver/` (the default). The script reads `MCP_SERVER_DIR` from
the **process environment**, adds that directory to `sys.path`, and imports the
`server` package from it. Point it at a different build with:

```
MCP_SERVER_DIR=/path/to/some-mcp-build
```

This matters because the imported build's `server.config` is what loads that
build's `.env` and therefore sets **`LOG_DIR`** (which logs the dashboard reads)
and **TLS** (`MCP_TLS_CERT` / `MCP_TLS_KEY`). `MCP_SERVER_DIR` must point at the
build whose logs you want to see; if it points at a build with a different
`LOG_DIR`, the dashboard reads an empty directory and renders "No data".

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

Run bare like this, it binds the defaults: `http://0.0.0.0:8002/dashboard`. To
change the port/build/log dir, set the env vars yourself before launching, e.g.:

```powershell
$env:MCP_SERVER_DIR    = "..\mqacemcpserver"
$env:MCP_DASHBOARD_PORT = "8004"
.\.venv\Scripts\python.exe dashboard_server.py
```

Most of the time you don't run it bare — `scripts\start-all.ps1` /
`start-all.sh` do this wiring for you (see below).

## Configuration

`dashboard_server.py` does **not** load any `.env` file of its own. It resolves
config from two places:

1. **Process environment** — `MCP_DASHBOARD_HOST`, `MCP_DASHBOARD_PORT`, and
   `MCP_SERVER_DIR` are read with `os.getenv` (defaults below).
2. **The imported build's `server.config`** — that module loads the build's own
   `.env` (`mqacemcpserver/.env` by default) and supplies `LOG_DIR` plus
   the TLS cert/key.

| Var | Read from | Default | Purpose |
| --- | --- | --- | --- |
| `MCP_DASHBOARD_HOST` | process env | `0.0.0.0` | Bind host. |
| `MCP_DASHBOARD_PORT` | process env | `8002` | Bind port. |
| `MCP_SERVER_DIR` | process env | `../mqacemcpserver` | Which build's `server` package to import (TLS config + fallback `LOG_DIR`). |
| `MCP_DASHBOARD_SERVERS_JSON` | process env | unset → single tab | JSON array of `{name,key,log_dir}`; one tab per entry. |
| `MCP_DASHBOARD_REFRESH_SECONDS` | process env | `60` | Auto-reload interval for each dashboard page; `0` disables. The wrapper's selected tab is preserved (only the inner page reloads). |
| `LOG_DIR` | build's `.env` | `<build>/logs` | Fallback single-tab log dir when the JSON above is unset. |
| `MCP_TLS_CERT` / `MCP_TLS_KEY` | build's `.env` | unset (HTTP) | Both set → serve HTTPS. |

### Per-server tabs

The dashboard renders **one tab per configured MCP server**. `GET /dashboard` is
a tabbed wrapper; `GET /dashboard/<key>` is that server's full dashboard for its
own log dir. The tab set comes from `MCP_DASHBOARD_SERVERS_JSON`; if it is unset
the dashboard shows a single tab from the imported build's `LOG_DIR`.

### `dashboard/.env` and the launchers

`dashboard/.env` documents the intended dashboard settings, but **the server
does not auto-load it**. Instead, `scripts/start-all.ps1` / `start-all.sh` read
`MCP_DASHBOARD_PORT` from it and inject it — along with `MCP_SERVER_DIR` (the MCP
build, for TLS) and `MCP_DASHBOARD_SERVERS_JSON` (the build's log dir,
`mqacemcpserver/logs`) — into the dashboard process. That is why, started
via `start-all`, the dashboard serves on
**`https://localhost:8004/dashboard`** with a tab for the MCP build, rather than
the bare-run defaults of `http://…:8002`.

The endpoint has **no authentication** by design — do not bind it to a publicly
reachable interface unless that is acceptable in your environment.
