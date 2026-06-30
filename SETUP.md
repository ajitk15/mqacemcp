# Setup

How to run the stack. For what each component is, the ports, and how they fit
together, see [`DESIGN.md`](DESIGN.md).

The four apps — `mqacemcpserver/`, `agent/`, `frontend/`, `dashboard/` — are each
independently deployable with their own `requirements.txt` and `.env` (copy from
`.env.example`). There is **no repo-root `.env`**.

## Prerequisites

- **Python 3.11+** on `PATH`
- **PowerShell** (Windows); `scripts/start-all.sh` for POSIX
- **git**
- Backend needs an **`OPENAI_API_KEY`**

## Platform infrastructure (IBM MQ + ACE)

Optional — the tools also work offline against `resources/`. Needs **IBM MQ** and
**IBM ACE** installed (commands on `PATH`). **Run MQ first, then ACE.** What each
script creates is described in [`DESIGN.md`](DESIGN.md#layers).

```bat
REM Windows (normal command prompt / IBM ACE console) — from the repo root
cd platform_build\mqsetup  & all_servers_full_setup.bat & cd ..\..
cd platform_build\acesetup & all_nodes_full_setup.bat   & cd ..\..
```
```bash
# Linux
. /opt/mqm/bin/setmqenv -s        ; cd platform_build/mqsetup  ; bash all_servers_full_setup.mqsc
. <ace>/server/bin/mqsiprofile    ; cd platform_build/acesetup ; bash all_nodes_full_setup.sh
```

## Option A — full stack

Use the `.cmd` wrappers — they run the PowerShell launchers with
`-ExecutionPolicy Bypass`, so they work even when Windows blocks `.ps1` scripts
(the default). Run from the repo root:

```bat
.\scripts\start-all.cmd -Setup     REM first run: build venvs + install, then start
.\scripts\start-all.cmd            REM subsequent runs
.\scripts\stop-all.cmd             REM stop
```

Prefer the `.ps1` launchers directly (`start-all.ps1`, `stop-all.ps1`,
`start-streamlit.ps1`)? They need script execution enabled, e.g. once per shell:
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.

First run, copy each `.env`:

```powershell
copy mqacemcpserver\.env.example mqacemcpserver\.env
copy agent\.env.example          agent\.env         # set OPENAI_API_KEY
copy frontend\.env.example       frontend\.env
copy dashboard\.env.example      dashboard\.env
```

`-Skip*` switches isolate a tier; ports come from each `.env` at runtime.

## Option B — one app at a time

From the repo root. See each app's `README.md` for full config.

```powershell
# MCP server (uses repo-root .venv + shared resources/)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r mqacemcpserver\requirements.txt
copy mqacemcpserver\.env.example mqacemcpserver\.env
$env:MCP_TRANSPORT="streamable-http"; .\.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py   # :8010 /mcp

# backend (own venv)
cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt; copy .env.example .env   # set OPENAI_API_KEY, MCP_SSE_URL
python app.py                                              # :8002

# frontend (own venv)
cd frontend; python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt; copy .env.example .env
streamlit run app.py --server.port 8003

# dashboard (own venv)
cd dashboard; python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt; copy .env.example .env
python dashboard_server.py                                # :8004 → /dashboard
```

## Verify

```powershell
curl.exe -k https://localhost:8010/healthz     # MCP
curl.exe http://localhost:8002/api/health      # backend
curl.exe http://localhost:8004/healthz         # dashboard
```
