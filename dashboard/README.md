# Dashboard component

A standalone HTTP server that renders the MQ + ACE **log-insights dashboard**
from the JSONL/text logs the MCP server writes. It runs in its own process and
its own venv, **fully self-contained** — it depends only on the stdlib,
`uvicorn`, `python-dotenv` and its sibling `analyze_logs.py`, and does **not**
import the MCP server's `server` package. It only *reads* the log files at the
configured `LOG_DIR`. All settings come from `dashboard/.env` (or the process
environment, which wins); see [Configuration](#configuration).

```
dashboard/
  dashboard_server.py   — ASGI app (uvicorn). GET /dashboard, GET /healthz
  analyze_logs.py       — pure-Python HTML/metrics builder (no third-party deps)
  requirements.txt      — uvicorn + python-dotenv
```

## Configuration source

`dashboard_server.py` loads its **own** `dashboard/.env` at startup (via
`python-dotenv`) and reads every setting from the environment — no `server`
package, no `sys.path` hacks, no `MCP_SERVER_DIR`. Process-env values take
precedence over the `.env` file, so a launcher can still inject overrides.

Point `LOG_DIR` at the log directory whose data you want to visualise (e.g. an
MCP build's `logs/`); if it points at an empty directory the dashboard renders
"No data".

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

Run bare like this, it loads `dashboard/.env` and binds whatever it configures
(defaults if unset: `http://0.0.0.0:8002/dashboard`). To change the port/log dir
ad hoc, either edit `dashboard/.env` or set env vars before launching, e.g.:

```powershell
$env:LOG_DIR            = "..\mqacemcpserver\logs"
$env:MCP_DASHBOARD_PORT = "8004"
.\.venv\Scripts\python.exe dashboard_server.py
```

Most of the time you don't run it bare — `scripts\start-all.ps1` /
`start-all.sh` do this wiring for you (see below).

## Configuration

`dashboard_server.py` loads `dashboard/.env` on startup and reads all config
from the environment (process-env overrides the file):

| Var | Default | Purpose |
| --- | --- | --- |
| `MCP_DASHBOARD_HOST` | `0.0.0.0` | Bind host. |
| `MCP_DASHBOARD_PORT` | `8002` | Bind port. |
| `MCP_DASHBOARD_SERVERS_JSON` | unset → single tab | JSON array of `{name,key,log_dir}`; one tab per entry. |
| `MCP_DASHBOARD_REFRESH_SECONDS` | `60` | Auto-reload interval for each dashboard page; `0` disables. The wrapper's selected tab is preserved (only the inner page reloads). |
| `LOG_DIR` | `<dir>/logs` | Fallback single-tab log dir when the JSON above is unset. Relative paths resolve under `dashboard/`. |
| `MCP_TLS_CERT` / `MCP_TLS_KEY` | unset (HTTP) | Both set → serve HTTPS. Relative paths resolve under `dashboard/`. |
| `DASHBOARD_THEME` | `purple` | Color palette: `purple` or `green`. |

### Theming

The dashboard ships two color palettes, `purple` (default) and `green` (MQ
green, ACE teal, success stays green). Set the default with `DASHBOARD_THEME`, or
override per request with a `?theme=purple|green` query on the `/dashboard` URL —
the tab wrapper propagates the choice to every tab, and unknown values fall back
to `purple`. Palettes are defined as token dicts in `analyze_logs.py` (`THEMES`),
so adding another color is a matter of adding an entry.

### Per-server tabs

The dashboard renders **one tab per configured MCP server**. `GET /dashboard` is
a tabbed wrapper; `GET /dashboard/<key>` is that server's full dashboard for its
own log dir. The tab set comes from `MCP_DASHBOARD_SERVERS_JSON`; if it is unset
the dashboard shows a single tab from `LOG_DIR`.

### `dashboard/.env` and the launchers

The dashboard auto-loads `dashboard/.env`, so bare runs pick it up directly.
`scripts/start-all.ps1` / `start-all.sh` additionally inject
`MCP_DASHBOARD_PORT` and `MCP_DASHBOARD_SERVERS_JSON` (the build's log dir,
`mqacemcpserver/logs`) into the dashboard process so, started via `start-all`,
it serves on **`https://localhost:8004/dashboard`** with a tab for the MCP build.
For HTTPS there, set `MCP_TLS_CERT` / `MCP_TLS_KEY` in `dashboard/.env` (the
dashboard no longer inherits TLS from the MCP build).

The endpoint has **no authentication** by design — do not bind it to a publicly
reachable interface unless that is acceptable in your environment.
