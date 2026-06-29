# Setup

This repo is a **mono-repo of four independently deployable apps** plus a shared
`resources/` folder. Each app owns its `requirements.txt`, its `.env` (copied
from the checked-in `.env.example`), and its `README.md`. There is **no
repo-root `.env`** — every app reads its own.

| App | What it is | Default port | Own `.env` |
| --- | --- | --- | --- |
| `mqacemcpserver/` | Unified MQ + ACE MCP server (composite "single-call" tools) | SSE `:8010` | `mqacemcpserver/.env` |
| `backend/` | Chatbot agent — FastAPI + LangGraph | `:8002` | `backend/.env` |
| `frontend/` | Chatbot UI — Streamlit | `:8003` | `frontend/.env` |
| `dashboard/` | Log-analytics dashboard (one tab per MCP server) | `:8004` | `dashboard/.env` |

**Shared at the repo root:** only `resources/` (the daily-extract CSV manifests
consumed by the MCP server) and the MCP build's dev `.venv`.

## Prerequisites

- **Python 3.11+** on `PATH`
- **PowerShell** (Windows) — the launchers are `.ps1`; a `start-all.sh` exists for POSIX
- **git**
- For the chatbot backend: an **`OPENAI_API_KEY`**

---

## Option A — full local stack (recommended)

The launcher creates any missing virtual-envs, installs each app's
`requirements.txt`, and opens one window per service.

```powershell
# from the repo root
.\scripts\start-all.ps1 -Setup     # first run: build venvs + install deps, then start
.\scripts\start-all.ps1            # subsequent runs: just start
.\scripts\stop-all.ps1            # stop everything
```

This brings up all four apps side by side. `-Skip*` switches isolate a tier
(e.g. `-SkipMcpSingle`, `-SkipBackend`, `-SkipMcp`). Actual bind ports/scheme
are printed in the banner — they come from each app's `.env` at runtime.

Before first start, copy each app's example config and fill in real values
(the launcher warns about any missing `.env`):

```powershell
copy mqacemcpserver\.env.example mqacemcpserver\.env
copy backend\.env.example               backend\.env       # set OPENAI_API_KEY
copy frontend\.env.example              frontend\.env
copy dashboard\.env.example             dashboard\.env
```

---

## Option B — run an app on its own

Every app is self-contained. Each section below assumes you start from the
repo root. See the app's own `README.md` for the full config reference.

### `mqacemcpserver/` — MCP server

Uses the repo-root `.venv` and reads the shared root `resources/`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r mqacemcpserver\requirements.txt
copy mqacemcpserver\.env.example mqacemcpserver\.env   # then edit MQ_*/ACE_* creds
# SSE on :8010 (it reads mqacemcpserver\.env regardless of cwd)
$env:MCP_TRANSPORT = "sse"
.\.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
```

### `backend/` — chatbot agent (FastAPI)

Its own venv; needs `OPENAI_API_KEY` and an MCP server reachable at `MCP_SSE_URL`
(defaults to the MCP server, `:8010`).

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # set OPENAI_API_KEY, MCP_SSE_URL, MCP_AUTH_*
python app.py                   # :8002
```

### `frontend/` — chatbot UI (Streamlit)

Its own venv; fronts the backend at `MCP_BACKEND_URL` (defaults to `:8002`).

```powershell
cd frontend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py --server.port 8003
```

### `dashboard/` — log-analytics dashboard

Its own venv; imports the MCP build's `server.config`/`server.logger` (for TLS +
log dir) via `MCP_SERVER_DIR`.

```powershell
cd dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python dashboard_server.py      # :8004 → /dashboard
```

---

## Verify

```powershell
# MCP health (always bypasses auth)
curl.exe -k https://localhost:8010/healthz
# backend + dashboard
curl.exe http://localhost:8002/api/health
curl.exe http://localhost:8004/healthz
```

Smoke-check that the MCP server registers its tools:

```powershell
cd mqacemcpserver
..\.venv\Scripts\python.exe -c "import mqacemcpserver as m; print(sorted(m.mcp._tool_manager._tools.keys()))"
```

## Notes

- `.env` files are git-ignored; only the `.env.example` templates are committed.
  Never commit real credentials.
- The MCP server reads `resources/` at the repo root so the daily extract job
  feeds it from one place — don't duplicate the CSVs.
- TLS is `verify=False` and the SSE endpoints use self-signed certs from
  `certs/` for local dev (hence `curl -k`).
- Per-app configuration tables live in each app's `README.md`; repo-wide
  guidance for Claude Code is in `CLAUDE.md`.
