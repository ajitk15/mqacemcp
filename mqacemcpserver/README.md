# IBM MQ + IBM ACE — Unified MCP Server

A single Model Context Protocol (MCP) server that exposes read-only diagnostic
tools for **IBM MQ** and **IBM App Connect Enterprise (ACE)**. Hand the central
team one endpoint; their orchestrator/LLM picks the right tool from the unified
tool list based on the user's question — no in-server routing required.

> New to MQ / ACE or unsure where they fit in an enterprise middleware
> stack? See **[docs/MIDDLEWARE_STACK.md](../docs/MIDDLEWARE_STACK.md)**
> for a short primer with ESB topology and layer-mapping diagrams.

> **Layout note:** this build lives in `mqacemcpserver/`. Its dev `.venv`
> stays at the **repo root** (shared); commands below run from the repo root
> unless stated otherwise. The server auto-detects whether it runs standalone
> (its own `resources/` beside the code) or in the mono-repo (shared root
> `resources/` and `.env`).

## Setup

```powershell
# from the project root: C:\Workspace\hready\mqacemcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r mqacemcpserver\requirements.txt
copy .env.example .env
# then edit .env with real MQ / ACE credentials and allow-list prefixes
```

Drop the inventory CSVs into `resources/`:

| File | Purpose | Format |
| --- | --- | --- |
| `resources/qmgr_dump.csv` | MQ object manifest | header row: `extractedat\|hostname\|qmname\|objecttype\|objectdef` |
| `resources/node_config.csv` | ACE node → host:port mapping | header row: `node\|host\|nodeport` |
| `resources/node_dump.csv` | ACE offline status dump | no header: `timestamp\|host\|node\|status` |

Sample versions of all three are checked into `resources/` so the server runs
out of the box for testing. Replace them with the real production extracts in a
real deployment.

## Connect a client

See **[docs/CONNECTING.md](../docs/CONNECTING.md)** for copy-paste configs for
Claude Desktop, Claude Code (CLI + VS Code extension), VS Code GitHub Copilot
agent mode, Cursor, the MCP Inspector, and the Python MCP SDK — plus a
troubleshooting matrix.

## Web chat UI (optional)

A standalone, MCP-server-agnostic chat UI lives in
**[backend/](../backend/README.md)** + **[frontend/](../frontend/README.md)**.
It pairs a FastAPI + LangGraph backend (OpenAI) with a Streamlit frontend in
`frontend/` (`app.py`, :8501). Features include: session memory, structured
rendering (tables / Mermaid / code blocks), a configurable scope guardrail
(`BOT_DOMAIN`), an externalised system prompt (`prompts/system.md`), and a tool
allow/deny list — all driven from `backend/.env`. The MCP server itself is
untouched; the chatbot talks to it over SSE like any other MCP client.

Launch the whole stack (MCP server + chat backend + UI) with:

```powershell
.\scripts\start-all.ps1
```

If you need to explain *why* the chatbot qualifies as agentic AI (vs. "a
chat box wrapping APIs"), point readers at
**[backend/AGENTIC_AI.md](../backend/AGENTIC_AI.md)** — a single doc that maps
the canonical agentic-AI components to specific files here. Curated demo
prompts live in **[backend/SAMPLE_QUESTIONS.md](../backend/SAMPLE_QUESTIONS.md)**.

## Run

**stdio (local/dev, default):**
```powershell
.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
```

**SSE (HTTP endpoint for the central team):**
```powershell
$env:MCP_TRANSPORT = "sse"
$env:MCP_AUTH_USER = "..."
$env:MCP_AUTH_PASSWORD = "..."
.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
# endpoint: http://<MCP_HOST>:<MCP_PORT>/sse
```

**SSE over HTTPS:** set `MCP_TLS_CERT` and `MCP_TLS_KEY` (both required) to
PEM-encoded files; the endpoint is then served at
`https://<MCP_HOST>:<MCP_PORT>/sse`. Use an unencrypted PEM key. Quick
self-signed cert for dev:
```powershell
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"
```

The same env vars can live in `.env` instead of being exported.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `MCP_HOST` | `0.0.0.0` | Bind address (SSE) |
| `MCP_PORT` | `8000` | Bind port (SSE) |
| `MCP_AUTH_USER` / `MCP_AUTH_PASSWORD` | — | Optional HTTP Basic Auth on the SSE endpoint. Both must be set to enable. |
| `MCP_TLS_CERT` / `MCP_TLS_KEY` | — | PEM cert + key paths for HTTPS on the SSE endpoint. Both must be set to enable. |
| `MQACE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_DIR` | `./logs` | Directory for application + query logs |
| `LOG_RETENTION_DAYS` | `30` | Old date-stamped log files are pruned at startup |
| `QUERY_LOG_ENABLED` | `true` | Toggle the per-call JSONL query log |
| `MQ_URL_BASE` | — | Base URL of an MQ web (mqweb) server. Trailing slash required. |
| `MQ_USER_NAME` / `MQ_PASSWORD` | — | MQ REST API credentials |
| `MQ_ALLOWED_HOSTNAME_PREFIXES` | `lod,loq,lot` | Comma-separated prefixes the MQ tools may talk to |
| `MQ_SUPPORT_TEAM` | `MQ Infra Support` | Display name in the "modification blocked" message |
| `MQ_ADMIN_GROUP` | `MQACE_ADMIN` | ServiceNow group in the same message |
| `ACE_USER_NAME` / `ACE_PASSWORD` | — | ACE Admin REST API credentials (optional) |
| `ACE_ALLOWED_HOSTNAME_PREFIXES` | `lod,loq,lot` | Comma-separated prefixes the ACE tools may talk to |

## Security model

- **Read-only MQSC.** Modification verbs (`ALTER`, `DEFINE`, `DELETE`, `CLEAR`,
  `MOVE`, `SET`, `RESET`, `START`, `STOP`, `PURGE`, `REFRESH`, `RESOLVE`,
  `ARCHIVE`, `BACKUP`) are blocked at the tool layer; the user is redirected to
  `MQ_SUPPORT_TEAM` / `MQ_ADMIN_GROUP`.
- **Read-only ACE.** Every ACE tool issues only HTTP `GET` against the Admin
  REST API. There is no deploy / start / stop tool.
- **Hostname allow-list (both halves).** Every outbound call resolves a target
  hostname, then checks the relevant `*_ALLOWED_HOSTNAME_PREFIXES`. Anything
  outside the list returns a friendly "restricted" message instead of being
  contacted. Tune the prefixes per environment (the defaults exclude
  production by convention).
- **Optional Basic Auth on SSE.** Only enabled when both `MCP_AUTH_USER` and
  `MCP_AUTH_PASSWORD` are set; otherwise the SSE endpoint is unauthenticated
  and a `WARNING` is logged at startup.
- **User-facing errors are sanitized.** When a remote system fails, the user
  sees a single short sentence ending in `(ref <id>)`. The full traceback,
  response body, URL, and host are written only to `logs/app-YYYY-MM-DD.log`
  tagged with the same `request_id` that appears in `queries-*.jsonl`, so
  support can correlate. No raw exception text or upstream response body
  reaches the user.

## Tools

### IBM MQ (7 tools, all read-only)

| Name | What it does |
| --- | --- |
| `find_mq_object` | Search the manifest for an object name; returns the queue manager(s), host(s), and type. |
| `dspmq` | List queue managers and their state on a given host (or the default mqweb). |
| `dspmqver` | Display IBM MQ version / installation info on a host. |
| `runmqsc` | Run a single read-only MQSC command against a known queue manager. |
| `run_mqsc_for_object` | Auto-discover hosting QMs for an object and run an MQSC command on each. |
| `get_queue_depth` | Get current depth across all hosting QMs; resolves alias queues to their target. |
| `get_channel_status` | Get channel status across all hosting QMs. |

### IBM ACE (6 tools, all read-only)

| Name | What it does |
| --- | --- |
| `list_ace_nodes` | List integration nodes from `node_config.csv`. |
| `get_ace_node_status` | Real-time status of an integration node (properties, version, platform). |
| `list_ace_servers` | List integration servers on a given node. |
| `list_ace_applications` | List applications deployed on a given integration server. |
| `list_ace_message_flows` | List message flows on a given integration server (optionally scoped to an application). |
| `search_ace_local_dump` | Offline-triage search across `node_dump.csv` (BIP messages incl. flow / app / server state). |

### Certificates (1 tool, all read-only)

| Name | What it does |
| --- | --- |
| `get_cert_details` | Offline lookup of TLS/SSL certificate details from `cert_dump.csv` (hostname, alias, cn_name, valid_from/valid_until, expirydays, ace_nodes). `valid_until` is the expiry date; `expirydays` is computed live (days until expiry, negative if expired); `ace_nodes` lists the ACE node(s) running on that host (empty for a pure-MQ host). Searches by hostname, alias, or CN. |

For a detailed per-tool walkthrough — inputs, resolution chain,
fallback behaviour, recorded endpoints — see
**[docs/TOOLS.md](../docs/TOOLS.md)**.

## How the orchestrator routes

The orchestrator's LLM sees all 14 tools. Every MQ docstring starts with
`IBM MQ:`, every ACE docstring with `IBM ACE:`, and the certificate docstring
with `Certificate:`. Tool names also encode the
product (`dspmq`, `runmqsc`, `list_ace_nodes`, `get_cert_details`, etc.). When a user asks
*"what queues are on QM1"* the LLM picks an MQ tool; when they ask
*"what integration servers are on NODE01"* it picks an ACE tool. No dispatcher
inside the server.

## Health check

The SSE app exposes an unauthenticated `GET /healthz` for ops monitors and
load balancers. It bypasses `BasicAuthMiddleware`, so liveness probes work
even when the rest of the endpoint is gated.

```
$ curl http://<MCP_HOST>:<MCP_PORT>/healthz
{"status":"ok","service":"mqacemcpserver","transport":"sse","mq_configured":true,"ace_configured":true,
 "manifests":[{"name":"Certificate inventory","file":"cert_dump.csv","exists":true,"rows":10,
               "file_mtime":"2026-06-08T18:07:07","loaded_at":"2026-06-08T20:32:10","stale":false}, ...]}
```

The `mq_configured` / `ace_configured` flags reflect whether the relevant
env vars and CSVs are present — they do **not** ping the upstream MQ/ACE
hosts (use the per-call query log for upstream observability).

The `manifests` array reports each offline CSV's freshness: `rows` currently in
memory, `file_mtime` (on disk), `loaded_at` (when it was last read into memory),
and `stale` (the file changed on disk and a reload is pending on next access).
`rows`/`loaded_at` are `null` until a tool first reads that manifest.

## Data freshness (auto-reload, no restart)

The four CSV manifests are replaced by a daily extract job. Each loader
(`load_csv`, `load_node_dump`, `load_node_config`, `load_cert_dump`) goes
through `server/csv_cache.py:CsvCache`, which checks the file's `(mtime, size)`
on every access and **reloads only when it changed** — so a daily swap is picked
up on the next tool call **with no restart**, at the cost of one `os.stat` per
call (the CSV is re-parsed only when it actually changes). If a read lands while
the file is mid-write (loader fails), the cache keeps serving the
previously-loaded data and retries on the next call rather than flapping to empty.

> **Ops note:** have the extract job write to a temp file and **atomically rename**
> it into place (e.g. `os.replace`), so a reader never sees a half-written CSV.

## Tests

A small offline pytest suite covers the safety primitives, query-log
decorator, error sanitiser, and the `runmqsc` allow-list path:

Run from **inside** `mqacemcpserver/` (both this build and
`mqacemcpserver-single/` ship a top-level `server` package, so a repo-root
pytest run would collide on the import name):

```powershell
cd mqacemcpserver
..\.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
..\.venv\Scripts\python.exe -m pytest -q
```

Tests redirect `LOG_DIR` to a temp directory via `tests/conftest.py` so they
never pollute the project's `logs/` folder.

## Logging

The server writes two file-based logs into `LOG_DIR` (default `./logs/`),
both rotated daily and pruned after `LOG_RETENTION_DAYS` days.

### Application log — `logs/app-YYYY-MM-DD.log`
Plain text. Mirrors stderr. Captures startup events, the MCP endpoint URL
(when SSE), env-var warnings, connectivity check results, and per-call
warnings/errors. Format:
```
2026-05-09 12:34:56,789 INFO [mqacemcpserver] MCP SSE endpoint: http://0.0.0.0:8000/sse
```

### Per-call query log — `logs/queries-YYYY-MM-DD.jsonl`
One JSON object per line, written on every MCP tool invocation. Designed for
direct ingestion into Power BI's "From Folder" connector. Schema:

| Field | Type | Notes |
| --- | --- | --- |
| `ts` | string | ISO 8601 UTC, millisecond precision (e.g. `2026-05-09T12:34:56.789Z`) |
| `request_id` | string | UUID hex; correlate with the application log |
| `transport` | string | `stdio` or `sse` |
| `caller` | string \| null | SSE Basic Auth username when set; `null` otherwise |
| `tool` | string | Tool name (`dspmq`, `runmqsc`, `list_ace_servers`, …) |
| `args` | object | Sanitized kwargs. Keys containing `password`/`secret`/`token`/`auth`/`pwd`/`key`/`credential` are replaced with `"[REDACTED]"` |
| `endpoints` | string[] | Ordered list of remote URLs the tool actually hit (MQ REST or ACE Admin URLs). Empty for local-only tools (`find_mq_object`, `search_ace_local_dump`, `list_ace_nodes`, `get_cert_details`) and for calls short-circuited by the allow-list |
| `outcome` | string | `success` or `error` |
| `error` | string \| null | `TypeName: message` on failure |
| `latency_ms` | int | End-to-end wall time |
| `response_bytes` | int \| null | Length of the string return value |

Example line:
```json
{"ts":"2026-05-09T12:34:56.789Z","request_id":"7f3a…","transport":"sse","caller":"alice","tool":"runmqsc","args":{"qmgr_name":"MQQMGR1","mqsc_command":"DISPLAY QMGR ALL"},"endpoints":["https://lodalhost:9443/ibmmq/rest/v2/admin/action/qmgr/MQQMGR1/mqsc"],"outcome":"success","error":null,"latency_ms":142,"response_bytes":8421}
```

Disable with `QUERY_LOG_ENABLED=false` in `.env` (the application log is
always written).

### Power BI ingestion

1. Power BI Desktop → **Get Data → More → File → Folder** → select
   `<project>\logs`.
2. Filter the file list to `queries-*.jsonl`; **Combine & Transform**.
3. In Power Query, **Transform → Parse JSON** on the combined column.
4. Expand `args`, `endpoints` (use *Expand to New Rows* for per-endpoint
   metrics), and the rest of the fields.
5. Suggested DAX measures:
   - Calls per tool: `COUNTROWS(Queries)` grouped by `tool`
   - Error rate: `DIVIDE(COUNTROWS(FILTER(Queries, [outcome]="error")), COUNTROWS(Queries))`
   - p95 latency by tool: `PERCENTILEX.INC(Queries, [latency_ms], 0.95)`
   - Top endpoints by hits: count after expanding `endpoints`
   - Calls per caller: requires SSE Basic Auth to be enabled

The same "From Folder" flow on `app-*.log` (treat as text, filter
`Contains("ERROR")` or `Contains("WARNING")`) gives an operational health
dashboard.

## Project layout

```
mqacemcp/                    # repo root
├── mqacemcpserver/          # THIS build — unified MQ + ACE MCP server
│   ├── mqacemcpserver.py    #   entry point
│   ├── server/
│   │   ├── config.py        #   .env loading, typed settings, standalone/mono-repo detection
│   │   ├── logger.py        #   stdlib logging factory + daily-rotated file handler
│   │   ├── query_log.py     #   per-call JSONL query log + logged_tool decorator
│   │   ├── errors.py        #   user-safe error sanitiser (ref-tagged messages)
│   │   ├── auth.py          #   Basic Auth ASGI middleware (SSE) + caller capture + /healthz bypass
│   │   ├── safety.py        #   hostname allow-list + read-only MQSC guard
│   │   ├── mq_helpers.py    #   MQ HTTP client, manifest, formatters, errors
│   │   ├── mq_tools.py      #   @mcp.tool wrappers for MQ
│   │   ├── ace_helpers.py   #   ACE HTTP client, node config / dump, REST helper
│   │   ├── ace_tools.py     #   @mcp.tool wrappers for ACE
│   │   └── cert_tools.py    #   @mcp.tool wrapper for certificate lookups
│   ├── tests/               #   offline pytest suite
│   ├── clients/             #   manual HTTPS smoke client
│   └── requirements.txt
├── mqacemcpserver-single/   # second build: one composite tool (own server/, tests/, requirements.txt)
├── backend/                 # chatbot stack — FastAPI + LangGraph agent (:8001). See backend/README.md.
├── frontend/                # chatbot stack — Streamlit UI (:8501). See frontend/README.md.
├── scripts/                 # start-all.ps1 / stop-all.ps1 / start-streamlit.ps1 / gen_basic_auth.py
├── resources/               # shared CSV manifests (qmgr_dump, node_config, node_dump, cert_dump)
├── docs/                    # overview / supplementary docs: CONNECTING.md, TOOLS.md, deck (.pptx)
├── logs/                    # app-*.log + queries-*.jsonl (runtime, gitignored; LOG_DIR can redirect)
├── .venv/                   # shared dev venv for the main build (gitignored)
├── .env / .env.example      # shared config (root)
├── render.yaml              # Render blueprint for backend + frontend
├── CLAUDE.md                # repo-specific guidance for Claude Code
└── README.md                # repo overview / component index
```

## Verification

After install (run from inside `mqacemcpserver/`):

```powershell
cd mqacemcpserver
..\.venv\Scripts\python.exe -c "import mqacemcpserver; print(sorted([t.name for t in mqacemcpserver.mcp._tool_manager._tools.values()]))"
```

Should print:
```
['dspmq', 'dspmqver', 'find_mq_object', 'get_ace_node_status', 'get_cert_details',
 'get_channel_status', 'get_queue_depth', 'list_ace_applications', 'list_ace_message_flows',
 'list_ace_nodes', 'list_ace_servers', 'run_mqsc_for_object', 'runmqsc', 'search_ace_local_dump']
```

Inspect interactively with the MCP Inspector (no network needed for offline tools):
```powershell
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
```
