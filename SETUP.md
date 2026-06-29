# Setup

Mono-repo of four independently deployable apps + a shared `resources/`. Each app
has its own `requirements.txt`, `.env` (copy from `.env.example`), and `README.md`.
There is **no repo-root `.env`**.

| App | What it is | Port | `.env` |
| --- | --- | --- | --- |
| `mqacemcpserver/` | MQ + ACE MCP server | SSE `:8010` | `mqacemcpserver/.env` |
| `backend/` | Chatbot agent (FastAPI + LangGraph) | `:8002` | `backend/.env` |
| `frontend/` | Chatbot UI (Streamlit) | `:8003` | `frontend/.env` |
| `dashboard/` | Log-analytics dashboard | `:8004` | `dashboard/.env` |

## Prerequisites

- **Python 3.11+** on `PATH`
- **PowerShell** (Windows); `scripts/start-all.sh` for POSIX
- **git**
- Backend needs an **`OPENAI_API_KEY`**

## Platform infrastructure (IBM MQ + ACE)

`platform_build/` provisions the demo middleware the tools inspect. Optional — the
tools also work offline against the seed CSVs in `resources/`. Needs **IBM MQ** and
**IBM ACE** installed (their commands on `PATH`). **Run MQ first, then ACE**
(ACE ties `NODE1`→`MQNODE1`, `NODE2`→`MQNODE2`).

```bat
REM Windows (normal command prompt / IBM ACE console)
cd /d C:\Workspace\accready\mqacemcp\platform_build\mqsetup  & all_servers_full_setup.bat
cd /d C:\Workspace\accready\mqacemcp\platform_build\acesetup & all_nodes_full_setup.bat
```
```bash
# Linux
. /opt/mqm/bin/setmqenv -s        ; cd platform_build/mqsetup  ; bash all_servers_full_setup.mqsc
. <ace>/server/bin/mqsiprofile    ; cd platform_build/acesetup ; bash all_nodes_full_setup.sh
```

MQ creates 5 QMs on `ACECLUSTER` (`MQREPO1`:1414, `MQQM1`:1415, `MQREPO2`:1416,
`MQNODE1`:1420, `MQNODE2`:1421, all `localhost`). ACE creates `NODE1`/`NODE2`,
each with 4 integration servers + 4 demo BARs.

## Option A — full stack

```powershell
.\scripts\start-all.ps1 -Setup     # first run: build venvs + install, then start
.\scripts\start-all.ps1            # subsequent runs
.\scripts\stop-all.ps1             # stop
```

First run, copy each `.env`:

```powershell
copy mqacemcpserver\.env.example mqacemcpserver\.env
copy backend\.env.example        backend\.env       # set OPENAI_API_KEY
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
$env:MCP_TRANSPORT="sse"; .\.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py   # :8010

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
