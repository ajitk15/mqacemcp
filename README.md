# MQ + ACE MCP platform

A mono-repo holding an independently deployable MCP server build for **IBM MQ**
and **IBM App Connect Enterprise (ACE)**, plus a self-contained chatbot stack that
consumes it. Each top-level folder is its own deliverable — own entry point, own
`requirements.txt`, deployable as a separate app/service.

## Components

| Folder | What it is | Detailed docs |
| --- | --- | --- |
| **[`mqacemcpserver/`](mqacemcpserver/README.md)** | The unified MQ + ACE MCP server exposing composite "single-call" tools over one SSE endpoint. | [`mqacemcpserver/README.md`](mqacemcpserver/README.md) |
| **[`backend/`](backend/README.md)** | Chatbot agent backend — FastAPI + LangGraph (OpenAI) on `:8002`, talks to an MCP server over SSE. | [`backend/README.md`](backend/README.md), [`backend/AGENTIC_AI.md`](backend/AGENTIC_AI.md) |
| **[`frontend/`](frontend/README.md)** | Chatbot UI — Streamlit (`:8003`), MCP-server-agnostic. | [`frontend/README.md`](frontend/README.md) |
| **[`dashboard/`](dashboard/README.md)** | Log analytics dashboard (`:8004`) — one tab per MCP server. | [`dashboard/README.md`](dashboard/README.md) |
| `scripts/` | PowerShell launchers (`start-all.ps1`, `start-streamlit.ps1`, `stop-all.ps1`) and ops tooling. Each `-Skip*` switch isolates a tier; no switches brings up the whole stack. | — |
| `resources/` | Shared CSV manifests (`qmgr_dump`, `node_config`, `node_dump`, `cert_dump`) consumed by the MCP server. Replaced by a daily extract job. | — |

## Shared vs. isolated

- **Shared at repo root:** only `resources/` (the daily-extract CSV manifests
  consumed by the MCP server) and the MCP build's dev `.venv`. There is
  **no repo-root `.env`**.
- **Isolated per component:** every app (`mqacemcpserver/`, `backend/`,
  `frontend/`, `dashboard/`) owns its entry point, `requirements.txt`,
  `README.md`, and its **own `.env`** (copy that folder's `.env.example`). Deploy
  any one folder on its own.

## Quick start (full local stack)

```powershell
# one-time: create the MCP build's venv at the repo root + install deps
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r mqacemcpserver\requirements.txt

# bring up MCP server (:8010) + chat backend (:8002) + Streamlit UI (:8003) + dashboard (:8004)
.\scripts\start-all.ps1
# stop everything
.\scripts\stop-all.ps1
```

Run just the MCP server:

```powershell
$env:MCP_TRANSPORT = "sse"
.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
```

For step-by-step setup of the full stack or any single app, see
[`SETUP.md`](SETUP.md). Each component's README has its configuration and
deployment details; repo-specific guidance for Claude Code lives in
[`CLAUDE.md`](CLAUDE.md).
