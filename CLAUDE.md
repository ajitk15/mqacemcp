# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single Model Context Protocol (MCP) server (`mqacemcpserver`) that
exposes read-only diagnostic tools for **IBM MQ** and **IBM App Connect
Enterprise (ACE)** under one endpoint. It exposes a small set of **composite
"single-call" tools** (one tool per common diagnostic intent) so a client that
can only invoke ONE tool per user turn still completes discovery-plus-execution
workflows in a single call. The hosting orchestrator's LLM picks the right tool
from the unified tool list — there is no in-server router. Production posture:
the central team consumes one Streamable HTTP endpoint (legacy SSE still
selectable); everything else (logging, sanitised
errors, allow-list, read-only enforcement) is in-process.

## Development commands

The build lives in `mqacemcpserver/`. Its dev `.venv` stays at the **repo
root** (shared), but its code, tests, and `requirements.txt` live in the build
folder. Paths in the architecture section below are relative to
`mqacemcpserver/`.

```powershell
# venv + deps (Windows) — venv at repo root, requirements in the build folder
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r mqacemcpserver\requirements.txt

# Run (stdio, default) — from repo root; cwd stays root so shared resources/ resolve.
# The build reads its own mqacemcpserver/.env regardless of cwd.
.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py

# Run (Streamable HTTP, default — endpoint at http://MCP_HOST:MCP_PORT/mcp, healthz at /healthz)
$env:MCP_TRANSPORT = "streamable-http"
.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
# (legacy SSE is still selectable: $env:MCP_TRANSPORT = "sse" -> /sse)

# Smoke check that the tools register (run from inside the build folder)
cd mqacemcpserver
..\.venv\Scripts\python.exe -c "import mqacemcpserver as m; print(sorted(m.mcp._tool_manager._tools.keys()))"

# Tests — run from INSIDE mqacemcpserver/ (the suite imports the build's
# top-level `server` package, so run pytest from the build folder).
cd mqacemcpserver
..\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio   # one-time
..\.venv\Scripts\python.exe -m pytest -q                           # full suite
..\.venv\Scripts\python.exe -m pytest tests/test_composite_tools.py -q  # single file
..\.venv\Scripts\python.exe -m pytest -k "redacts" -q              # by name
```

`mqacemcpserver/tests/conftest.py` redirects `LOG_DIR` to a temp directory
**before** `server.config` is imported. Do not move that fixture out of
`conftest.py`, and do not import from `server.*` at the top of `conftest.py`
itself — the env vars must be set first.

## Big-picture architecture

### Tool routing without a dispatcher
Every MQ tool's docstring opens with `IBM MQ:`, every ACE tool's with `IBM ACE:`,
and the certificate tool's with `Certificate:`.
Tool **names** are also disambiguated.
The orchestrator's LLM uses these to route — preserve both conventions whenever
adding or renaming a tool, otherwise routing degrades silently.

### Decorator stack on every MCP tool
```python
@mcp.tool()       # outer — registers with FastMCP
@logged_tool      # inner — emits one JSONL line per call to logs/queries-*.jsonl
async def my_tool(...): ...
```
Order matters. FastMCP introspects `inspect.signature` which follows
`functools.wraps`'s `__wrapped__` set by `@logged_tool`. Reversing the order
breaks tool registration. The composite tools live in `server/composite_tools.py`
inside `register(mcp)`.

### Safety is enforced in three places, do not bypass any of them
1. **Hostname allow-list** (`server/safety.py:is_hostname_allowed`) — every outbound
   call resolves a target hostname, then checks it against
   `MQ_ALLOWED_HOSTNAME_PREFIXES` or `ACE_ALLOWED_HOSTNAME_PREFIXES`. There are
   **two separate allow-lists** (MQ and ACE infra typically live on different host
   families). Wrappers in `mq_helpers.py` (`hostname_allowed`) and `ace_helpers.py`
   (`hostname_allowed`) call into the shared primitive with the right list.
2. **Read-only MQSC** (`server/safety.py:is_modification_command`) — MQSC paths
   block ALTER/DEFINE/DELETE/CLEAR/MOVE/SET/RESET/START/STOP/
   PURGE/REFRESH/RESOLVE/ARCHIVE/BACKUP and return `MODIFY_BLOCKED_MSG` instead.
3. **Allow-list precedes every HTTP call**, including the unknown-QM path. There
   must be no silent fall-through that uses the QM name as a hostname when the
   manifest doesn't list it — that's a security bug. The current code rejects
   unknown QMs with no explicit hostname; do not reintroduce the fallback.

### Error sanitisation contract
**No tool ever returns raw exception text or upstream response bodies.** Both
`server/mq_helpers.py:friendly_error` and `server/ace_helpers.py:fetch_ace` route
all caught exceptions through `server/errors.py:safe_error_message`, which:
1. Reads `request_id` from `server.query_log._current_query` (set by `@logged_tool`).
2. Writes the full traceback to `logs/app-YYYY-MM-DD.log` via `logger.exception`.
3. Returns `f"⚠️ {curated_hint} (ref {request_id})"` to the user.

When adding a new code path that catches an exception, route it through
`safe_error_message` — never `str(err)` or `err.response.text` to the user.

### Observability is via two ContextVars
- `_current_query` (`server/query_log.py`) — set by `@logged_tool`, holds the
  in-flight record. Helpers stamp endpoints onto it via `record_endpoint(url)`.
  When you add a new outbound call site, **call `record_endpoint(url)` before
  the HTTP request** (or use the `mq_get`/`mq_post` wrappers in `mq_helpers.py`
  that do it for you). ACE side: `fetch_ace` already calls it.
- `_current_caller` — set by `BasicAuthMiddleware` after a successful Basic Auth
  check (SSE only). Populates the `caller` field in JSONL.

### CSV manifests are offline (and auto-reload on change)
`resources/qmgr_dump.csv`, `resources/node_dump.csv`, `resources/node_config.csv`,
and `resources/cert_dump.csv` are extracts produced by external jobs. Tools that
read them say "OFFLINE" in their docstring — the freshness depends on the CSV's
`extractedat`/`timestamp` columns (or the extract's run time), not on a live system.

These are replaced by a daily extract job, so the loaders **must not** cache
load-once-forever. Every `load_*` goes through `server/csv_cache.py:CsvCache`,
which `stat()`s the file on each access and reloads only when `(mtime, size)`
changed — the daily swap is picked up on the next call **with no restart**. When
adding a manifest, wrap its `_load_*_from_disk` (which returns `None` on
missing/parse-error so the cache keeps last-good) in a `CsvCache` and keep the
public `load_*()` returning `cache.get()`. Do **not** reintroduce a
`if _CACHE is None` global. `/healthz` exposes per-manifest freshness via
`csv_cache.all_status()`.

### Two HTTP clients, one shutdown path
`server/mq_helpers.py:get_http_client` and `server/ace_helpers.py:get_http_client`
each maintain a singleton `httpx.AsyncClient` with their own credentials. Both
are closed via `aclose_http_client` in `mqacemcpserver/mqacemcpserver.py:_shutdown`'s
finally block. Do not create ad-hoc clients in tools — use `mq_get`/`mq_post` for
MQ and `fetch_ace` for ACE.

### Adding a new tool — minimum checklist
1. Implement in `server/composite_tools.py` inside `register(mcp)`.
2. Both decorators in the right order (`@mcp.tool()` then `@logged_tool`).
3. Docstring opens with `IBM MQ:`, `IBM ACE:`, or `Certificate:` as appropriate.
4. Make MQ HTTP calls via `mq_get` / `mq_post` (not the raw client) so the
   endpoint gets recorded; make ACE REST calls via
   `fetch_ace(node, path, component, ...)` which handles endpoint resolution,
   allow-list, recording, and error sanitisation.
5. For MQ, resolve hostname then call `hostname_allowed(...)` before any HTTP call.
6. Wrap exceptions with `friendly_error` (which goes through `safe_error_message`).

## Logging contract for Power BI

Two file-based logs in `LOG_DIR` (default `<build>/logs/`), daily-rotated:
- `app-YYYY-MM-DD.log` — plain text, mirrors stderr.
- `queries-YYYY-MM-DD.jsonl` — one JSON object per tool invocation. Schema in
  `mqacemcpserver/README.md` "Logging" section. Power BI ingests via "Get Data → From Folder".

Sensitive kwargs are auto-redacted: any kwarg whose lowercase name contains
`password`, `secret`, `token`, `auth`, `pwd`, `key`, or `credential` is replaced
with `"[REDACTED]"`. To opt a parameter into redaction, name it accordingly.

## Environment variables

Loaded by `mqacemcpserver/server/config.py` at import time from **this
build's own** `mqacemcpserver/.env` (each app in the repo is independent
and owns its `.env`; there is no repo-root `.env`). CSV-manifest/log paths still
auto-detect standalone (own `resources/` beside the code) vs. mono-repo (shared
root `resources/`). The full table is in `mqacemcpserver/README.md`. Two
namespaces operators most often touch:
- `MQ_ALLOWED_HOSTNAME_PREFIXES` / `ACE_ALLOWED_HOSTNAME_PREFIXES` — comma-separated
  hostname prefixes; defaults `lod,loq,lot` (excludes prod by convention).
- `MCP_TRANSPORT` (`streamable-http` default → `/mcp`, `sse` → `/sse`, `stdio`),
  `MCP_AUTH_USER` + `MCP_AUTH_PASSWORD`
  (enables Basic Auth on the HTTP endpoint; `/healthz` always bypasses auth).

## Things that are deliberately NOT done

- **TLS verification is hardcoded `verify=False`** in both helpers. The user
  has explicitly opted to keep it that way for now; do not change without asking.
- **`requirements.txt` uses `>=` not `==`.** Same — explicit user choice.
- **`pytest`/`pytest-asyncio` are not in `requirements.txt`.** They live only
  in the dev `.venv`. If you change tests, document the install step.

## The `agent/` + `frontend/` chatbot stack (separate product)

`agent/` and `frontend/` together are a self-contained web chat UI + agent
backend that *uses* this MCP server over its SSE endpoint. They are **not**
part of the MCP server and the MCP server does not depend on them. Treat them
as separate products in one repo, each independently deployable (own
`requirements.txt`, own `.env`, own venv). See `agent/README.md` for full docs.

### Architecture summary
- `agent/` — FastAPI on `:8002`. LangGraph `create_react_agent`
  with `MemorySaver` (per-`thread_id` in-process). Tools loaded via
  `langchain-mcp-adapters.MultiServerMCPClient` pointed at `MCP_SSE_URL`.
- `frontend/` — **Streamlit** app (Python: `app.py`, `client.py`,
  `renderers.py`) on `:8003`. Streams from the backend over SSE via `httpx`.
  (There is no Next.js frontend in this repo despite older references; the
  Streamlit app lives directly in `frontend/`.)
- `scripts/start-all.ps1` / `start-streamlit.ps1` / `stop-all.ps1` — launchers
  that pre-flight prereqs and spawn the service windows. `start-all.ps1` brings up
  the MCP server (`mqacemcpserver`, :8010, reads its own `.env`) plus
  backend :8002, Streamlit UI :8003, dashboard :8004. The backend defaults to the
  MCP server (:8010); the Streamlit sidebar lets a user switch to a custom MCP URL
  at runtime (see backend `MCP_SERVERS_JSON` and `/api/mcp/connect`). `-SkipMcp`
  skips the MCP server; other `-Skip*` switches isolate a tier. Both
  `start-all.ps1` and `start-streamlit.ps1` launch the Streamlit UI from `frontend/`.
- The dashboard process (`dashboard/dashboard_server.py`) does **not** load
  `dashboard/.env` itself — it reads `MCP_DASHBOARD_PORT` / `MCP_SERVER_DIR` /
  `MCP_DASHBOARD_SERVERS_JSON` from the process environment and gets TLS from the
  imported build's `server.config`. It renders **one tab per configured MCP server**:
  `/dashboard` is a tabbed wrapper, `/dashboard/<key>` is that server's full
  dashboard for its log dir. `start-all.*` injects `MCP_SERVER_DIR`
  (`mqacemcpserver`, for TLS), `MCP_DASHBOARD_PORT`, and
  `MCP_DASHBOARD_SERVERS_JSON` (the build's log dir, `mqacemcpserver/logs`).
  If you launch the dashboard another way, set those env vars yourself or it falls
  back to a single tab from the imported `server.config` `LOG_DIR`.

### Hard rules when working in this repo
- **Do not modify any file under `mqacemcpserver/` or `resources/` from
  chatbot work.** The MCP server is untouched by design; the chatbot talks to
  it like any external client. If the chatbot needs a new behaviour, change the
  chatbot, not the server.
- **The frontend is MCP-server-agnostic.** No tool names, no MQ/ACE
  strings. All UI customisation (header title/subtitle, scope hint,
  empty-state) flows from backend `/api/health` → `frontend/client.py`
  → `frontend/app.py`.
- **The renderers (`agent/renderers.py` and the frontend's
  `frontend/renderers.py`) are tool-name-agnostic.** Use shape
  detection (JSON list keys, `key:value` lines, mermaid fences) — never
  branch on a tool name.

### Configuration knobs (all live in `agent/.env`)
| Var | Purpose |
| --- | --- |
| `MCP_SSE_URL` | The DEFAULT MCP server activated at startup. |
| `MCP_SERVERS_JSON` | Registry of selectable servers (`name`/`url`/`prompt_file`/`default`) shown in the sidebar dropdown. Each can map to its own prompt. Falls back to a single entry from `MCP_SSE_URL`. |
| `MCP_AUTH_USER` / `MCP_AUTH_PASSWORD` | Basic Auth for the MCP HTTP endpoint (shared by all registry servers). |
| `MCP_HEADERS_JSON` | Bearer / custom headers (escape hatch). |
| `HEADER_TITLE` / `HEADER_SUBTITLE` | UI title bar; subtitle override. |
| `BOT_DOMAIN` | Scope guardrail; empty = unrestricted. |
| `SYSTEM_PROMPT_FILE` | Override prompt file path. Default is `agent/prompts/system.md`. |
| `TOOL_ALLOWLIST` / `TOOL_DENYLIST` | Filter which MCP tools the agent sees. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | LLM. |

### Where common changes go
- Edit the system prompt → `agent/prompts/system.md` (markdown,
  uses `{scope_block}` and `{tool_catalog}` placeholders).
- Add a new structured rendering rule → `agent/renderers.py`
  (a new detector, NOT a per-tool function).
- Add a new wire-protocol event kind / `Block` shape → `agent/schemas.py`
  AND the frontend renderer in `frontend/renderers.py` (which dispatches
  on `block.kind`).
- Change the theme → Streamlit theming via `PAGE_TITLE` / `PAGE_ICON` in
  `frontend/.env`, or a `frontend/.streamlit/config.toml`
  `[theme]` block. (The old Tailwind `tailwind.config.ts` / `app/globals.css`
  no longer exist.)
