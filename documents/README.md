# IBM MQ + IBM ACE — Unified MCP Server

A single Model Context Protocol (MCP) server that exposes read-only diagnostic
tools for **IBM MQ** and **IBM App Connect Enterprise (ACE)**. Hand the central
team one endpoint; their orchestrator/LLM picks the right tool from the unified
tool list based on the user's question — no in-server routing required.

> New to MQ / ACE or unsure where they fit in an enterprise middleware
> stack? See **[documents/MIDDLEWARE_STACK.md](documents/MIDDLEWARE_STACK.md)**
> for a short primer with ESB topology and layer-mapping diagrams.

## Setup

```powershell
# from the project root: C:\Workspace\hready\mqacemcp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
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

See **[documents/CONNECTING.md](documents/CONNECTING.md)** for copy-paste configs for
Claude Desktop, Claude Code (CLI + VS Code extension), VS Code GitHub Copilot
agent mode, Cursor, the MCP Inspector, and the Python MCP SDK — plus a
troubleshooting matrix.

## Web chat UI (optional)

A standalone, MCP-server-agnostic chat UI lives under
**[chatbot/](chatbot/README.md)**. It pairs a FastAPI + LangGraph backend
(OpenAI GPT-4o) with two interchangeable frontends: the original Next.js 15
+ Tailwind UI in `chatbot/frontend/`, and an alternative Streamlit UI in
`chatbot/streamlit_frontend/` — same backend, pick whichever you prefer.
Features include: session memory, structured rendering (tables / Mermaid /
code blocks), a configurable scope guardrail (`BOT_DOMAIN`), an externalised
system prompt (`prompts/system.md`), and a tool allow/deny list — all driven
from `chatbot/backend/.env`. The MCP server itself is untouched; the chatbot
talks to it over SSE like any other MCP client.

Launch the whole stack (MCP server + chat backend + UI) with:

```powershell
.\scripts\start-all.ps1
```

If you need to explain *why* the chatbot qualifies as agentic AI (vs. "a
chat box wrapping APIs"), point readers at
**[chatbot/AGENTIC_AI.md](chatbot/AGENTIC_AI.md)** — a single doc that maps
the canonical agentic-AI components to specific files here. Curated demo
prompts live in **[chatbot/SAMPLE_QUESTIONS.md](chatbot/SAMPLE_QUESTIONS.md)**.

## Run

**stdio (local/dev, default):**
```powershell
.venv\Scripts\python.exe mqacemcpserver.py
```

**SSE (HTTP endpoint for the central team):**
```powershell
$env:MCP_TRANSPORT = "sse"
$env:MCP_AUTH_USER = "..."
$env:MCP_AUTH_PASSWORD = "..."
.venv\Scripts\python.exe mqacemcpserver.py
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
| `get_cert_details` | Offline lookup of TLS/SSL certificate details from `cert_dump.csv` (hostname, alias, cn_name, valid_from/valid_until, expirydays — computed live, negative if already expired — and ace_nodes, the ACE node(s) on that host, empty for a pure-MQ host). Searches by hostname, alias, or CN. |

For a detailed per-tool walkthrough — inputs, resolution chain,
fallback behaviour, recorded endpoints — see
**[documents/TOOLS.md](documents/TOOLS.md)**.

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
{"status":"ok","service":"mqacemcpserver","transport":"sse","mq_configured":true,"ace_configured":true}
```

The `mq_configured` / `ace_configured` flags reflect whether the relevant
env vars and CSVs are present — they do **not** ping the upstream MQ/ACE
hosts (use the per-call query log for upstream observability).

## Tests

A small offline pytest suite covers the safety primitives, query-log
decorator, error sanitiser, and the `runmqsc` allow-list path:

```powershell
.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
.venv\Scripts\python.exe -m pytest -q
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
mqacemcp/
├── mqacemcpserver.py        # entry point
├── server/
│   ├── config.py            # .env loading, typed settings
│   ├── logger.py            # stdlib logging factory + daily-rotated file handler
│   ├── query_log.py         # per-call JSONL query log + logged_tool decorator
│   ├── errors.py            # user-safe error sanitiser (ref-tagged messages)
│   ├── auth.py              # Basic Auth ASGI middleware (SSE) + caller capture + /healthz bypass
│   ├── safety.py            # hostname allow-list + read-only MQSC guard
│   ├── mq_helpers.py        # MQ HTTP client, manifest, formatters, errors
│   ├── mq_tools.py          # @mcp.tool wrappers for MQ
│   ├── ace_helpers.py       # ACE HTTP client, node config / dump, REST helper
│   └── ace_tools.py         # @mcp.tool wrappers for ACE
├── tests/                   # offline pytest suite
├── logs/                    # app-*.log + queries-*.jsonl (created at runtime, gitignored;
│                            #   LOG_DIR in .env can redirect elsewhere, e.g. custom-logs/)
├── resources/
│   ├── qmgr_dump.csv
│   ├── node_config.csv
│   └── node_dump.csv
├── chatbot/                 # separate stack: FastAPI + LangGraph backend +
│   ├── backend/             #   Next.js / Streamlit frontends (uses this MCP
│   ├── frontend/            #   server over SSE like any external client).
│   ├── streamlit_frontend/  #   See chatbot/README.md.
│   ├── AGENTIC_AI.md
│   ├── SAMPLE_QUESTIONS.md
│   └── README.md
├── scripts/                 # start-all.ps1 / stop-all.ps1 / start-streamlit.ps1 /
│                            #   gen_basic_auth.py
├── documents/               # supplementary docs: CONNECTING.md, EXECUTIVE_NARRATIVE.md,
│                            #   executive deck (.pptx)
├── requirements.txt
├── pytest.ini
├── .env.example
├── .gitignore
├── CLAUDE.md                # repo-specific guidance for Claude Code
└── README.md
```

## Verification

After install:

```powershell
.venv\Scripts\python.exe -c "import mqacemcpserver; print(sorted([t.name for t in mqacemcpserver.mcp._tool_manager._tools.values()]))"
```

Should print:
```
['dspmq', 'dspmqver', 'find_mq_object', 'get_ace_node_status', 'get_cert_details',
 'get_channel_status', 'get_queue_depth', 'list_ace_applications', 'list_ace_message_flows',
 'list_ace_nodes', 'list_ace_servers', 'run_mqsc_for_object', 'runmqsc', 'search_ace_local_dump']
```

Inspect interactively with the MCP Inspector (no network needed for offline tools):
```powershell
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe mqacemcpserver.py
```
