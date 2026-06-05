# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single Model Context Protocol (MCP) server (`mqacemcpserver`) that exposes
read-only diagnostic tools for **IBM MQ** and **IBM App Connect Enterprise (ACE)**
under one endpoint. The hosting orchestrator's LLM picks the right tool from the
unified tool list — there is no in-server router. Production posture: the central
team consumes one SSE endpoint; everything else (logging, sanitised errors, allow-list,
read-only enforcement) is in-process.

## Development commands

```powershell
# venv + deps (Windows)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run (stdio, default)
.venv\Scripts\python.exe mqacemcpserver.py

# Run (SSE — endpoint at http://MCP_HOST:MCP_PORT/sse, healthz at /healthz)
$env:MCP_TRANSPORT = "sse"
.venv\Scripts\python.exe mqacemcpserver.py

# Smoke check that all 14 tools register
.venv\Scripts\python.exe -c "import mqacemcpserver as m; print(sorted(m.mcp._tool_manager._tools.keys()))"

# Tests (pytest + pytest-asyncio installed in .venv, NOT in requirements.txt)
.venv\Scripts\python.exe -m pip install pytest pytest-asyncio   # one-time
.venv\Scripts\python.exe -m pytest -q                            # full suite
.venv\Scripts\python.exe -m pytest tests/test_mq_offline.py -q   # single file
.venv\Scripts\python.exe -m pytest -k "redacts" -q               # by name
```

`tests/conftest.py` redirects `LOG_DIR` to a temp directory **before** `server.config`
is imported. Do not move that fixture out of `conftest.py`, and do not import from
`server.*` at the top of `conftest.py` itself — the env vars must be set first.

## Big-picture architecture

### Tool routing without a dispatcher
Every MQ tool's docstring opens with `IBM MQ:`, every ACE tool's with `IBM ACE:`,
and the certificate tool's with `Certificate:`.
Tool **names** are also disambiguated (`dspmq`, `runmqsc`, `list_ace_nodes`,
`get_cert_details`, …).
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
breaks tool registration.

### Safety is enforced in three places, do not bypass any of them
1. **Hostname allow-list** (`server/safety.py:is_hostname_allowed`) — every outbound
   call resolves a target hostname, then checks it against
   `MQ_ALLOWED_HOSTNAME_PREFIXES` or `ACE_ALLOWED_HOSTNAME_PREFIXES`. There are
   **two separate allow-lists** (MQ and ACE infra typically live on different host
   families). Wrappers in `mq_helpers.py` (`hostname_allowed`) and `ace_helpers.py`
   (`hostname_allowed`) call into the shared primitive with the right list.
2. **Read-only MQSC** (`server/safety.py:is_modification_command`) — `runmqsc` and
   `run_mqsc_for_object` block ALTER/DEFINE/DELETE/CLEAR/MOVE/SET/RESET/START/STOP/
   PURGE/REFRESH/RESOLVE/ARCHIVE/BACKUP and return `MODIFY_BLOCKED_MSG` instead.
3. **Allow-list precedes every HTTP call**, including the unknown-QM path in
   `runmqsc`. The previous version had a silent fall-through (used the QM name as
   a hostname when the manifest didn't list it) — that's a security bug. The
   current code rejects unknown QMs with no explicit hostname; do not reintroduce
   the fallback.

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

### CSV manifests are offline
`resources/qmgr_dump.csv`, `resources/node_dump.csv`, `resources/node_config.csv`,
and `resources/cert_dump.csv` are extracts produced by external jobs. Tools that
read them (`find_mq_object`, `search_ace_local_dump`, `get_cert_details`) say
"OFFLINE" in their docstring — the freshness depends on the CSV's
`extractedat`/`timestamp` columns (or the extract's run time), not on a live system.

### Two HTTP clients, one shutdown path
`server/mq_helpers.py:get_http_client` and `server/ace_helpers.py:get_http_client`
each maintain a singleton `httpx.AsyncClient` with their own credentials. Both
are closed via `aclose_http_client` in `mqacemcpserver.py:_shutdown`'s finally
block. Do not create ad-hoc clients in tools — use `mq_get`/`mq_post` for MQ
and `fetch_ace` for ACE.

### Adding a new MQ tool — minimum checklist
1. Implement in `server/mq_tools.py` inside `register(mcp)`.
2. Both decorators in the right order (`@mcp.tool()` then `@logged_tool`).
3. Docstring opens with `IBM MQ:`.
4. Make HTTP calls via `mq_get` / `mq_post` (not the raw client) so the endpoint
   gets recorded.
5. Resolve hostname, then call `hostname_allowed(...)` before any HTTP call.
6. Wrap exceptions with `friendly_error` (which goes through `safe_error_message`).

### Adding a new ACE tool — minimum checklist
1. Implement in `server/ace_tools.py` inside `register(mcp)`.
2. Both decorators (same order).
3. Docstring opens with `IBM ACE:`. Tool name uses `ace_` prefix or contains `ace`.
4. Make REST calls via `fetch_ace(node, path, component, ...)` — it handles
   endpoint resolution, allow-list, recording, and error sanitisation.

## Logging contract for Power BI

Two file-based logs in `LOG_DIR` (default `<project>/logs/`), daily-rotated:
- `app-YYYY-MM-DD.log` — plain text, mirrors stderr.
- `queries-YYYY-MM-DD.jsonl` — one JSON object per tool invocation. Schema in
  README.md "Logging" section. Power BI ingests via "Get Data → From Folder".

Sensitive kwargs are auto-redacted: any kwarg whose lowercase name contains
`password`, `secret`, `token`, `auth`, `pwd`, `key`, or `credential` is replaced
with `"[REDACTED]"`. To opt a parameter into redaction, name it accordingly.

## Environment variables

Loaded from `.env` (project root) by `server/config.py` at import time. The full
table is in README.md. Two namespaces operators most often touch:
- `MQ_ALLOWED_HOSTNAME_PREFIXES` / `ACE_ALLOWED_HOSTNAME_PREFIXES` — comma-separated
  hostname prefixes; defaults `lod,loq,lot` (excludes prod by convention).
- `MCP_TRANSPORT` (`stdio` / `sse`), `MCP_AUTH_USER` + `MCP_AUTH_PASSWORD`
  (enables Basic Auth on SSE; `/healthz` always bypasses auth).

## Things that are deliberately NOT done

- **TLS verification is hardcoded `verify=False`** in both helpers. The user
  has explicitly opted to keep it that way for now; do not change without asking.
- **`requirements.txt` uses `>=` not `==`.** Same — explicit user choice.
- **`pytest`/`pytest-asyncio` are not in `requirements.txt`.** They live only
  in the dev `.venv`. If you change tests, document the install step.

## The `chatbot/` subdirectory (separate stack)

`chatbot/` is a self-contained web chat UI + agent backend that *uses* this
MCP server over its SSE endpoint. It is **not** part of the MCP server and
the MCP server does not depend on it. Treat them as two products in one
repo. See `chatbot/README.md` for full docs.

### Architecture summary
- `chatbot/backend/` — FastAPI on `:8001`. LangGraph `create_react_agent`
  with `MemorySaver` (per-`thread_id` in-process). Tools loaded via
  `langchain-mcp-adapters.MultiServerMCPClient` pointed at `MCP_SSE_URL`.
- `chatbot/frontend/` — Next.js 15 App Router, Tailwind, light theme. SSE
  streaming from a Server Component proxy route.
- `scripts/start-all.ps1` / `stop-all.ps1` — launchers that pre-flight
  prereqs and spawn three terminal windows.

### Hard rules when working in this repo
- **Do not modify any file under `server/`, `mqacemcpserver.py`, or
  `resources/` from chatbot work.** The MCP server is untouched by design;
  the chatbot talks to it like any external client. If the chatbot needs
  a new behaviour, change the chatbot, not the server.
- **The frontend is MCP-server-agnostic.** No tool names, no MQ/ACE
  strings. All UI customisation (header title/subtitle, scope hint,
  empty-state) flows from backend `/api/health` → `lib/backend-info.ts`
  → `app/page.tsx`.
- **The backend's renderers (`renderers.py`) are tool-name-agnostic.**
  Use shape detection (JSON list keys, `key:value` lines, mermaid
  fences) — never branch on a tool name.

### Configuration knobs (all live in `chatbot/backend/.env`)
| Var | Purpose |
| --- | --- |
| `MCP_SSE_URL` | Which MCP server to talk to. |
| `MCP_AUTH_USER` / `MCP_AUTH_PASSWORD` | Basic Auth for SSE. |
| `MCP_HEADERS_JSON` | Bearer / custom headers (escape hatch). |
| `HEADER_TITLE` / `HEADER_SUBTITLE` | UI title bar; subtitle override. |
| `BOT_DOMAIN` | Scope guardrail; empty = unrestricted. |
| `SYSTEM_PROMPT_FILE` | Override prompt file path. Default is `chatbot/backend/prompts/system.md`. |
| `TOOL_ALLOWLIST` / `TOOL_DENYLIST` | Filter which MCP tools the agent sees. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | LLM. |

### Where common changes go
- Edit the system prompt → `chatbot/backend/prompts/system.md` (markdown,
  uses `{scope_block}` and `{tool_catalog}` placeholders).
- Add a new structured rendering rule → `chatbot/backend/renderers.py`
  (a new detector, NOT a per-tool function).
- Add a new wire-protocol event kind → `chatbot/backend/schemas.py` AND
  `chatbot/frontend/lib/types.ts` AND `chatbot/frontend/components/chat/ChatPane.tsx`.
- Change the theme → `chatbot/frontend/tailwind.config.ts` palette +
  `chatbot/frontend/app/globals.css` body/markdown styles.
