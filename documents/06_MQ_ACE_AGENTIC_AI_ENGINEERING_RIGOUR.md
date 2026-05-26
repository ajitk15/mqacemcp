# Slide 06 — Engineering Rigour: What Makes This Agentic AI Production-Ready

**Sub-headline:** *Capabilities you can trust, controls you can prove, craft you can audit.*

> Voice of the slide: **us — the MQ/ACE Platform Support team.** Not "we built a chatbot" — "we built a *governed system* with a chatbot on the front."

---

## The three pillars

| **CAPABILITIES** — what it can do | **CONTROLS** — how it's bounded | **CRAFT** — how it's built |
|---|---|---|
| Autonomous, multi-step reasoning across the whole estate | Read-only **by construction**, not by config | Single-route enforcement — every call goes through one pipeline |
| 13 read-only diagnostic tools, plain-English interface | Defense-in-depth: 6 independent security layers | Decorator stack — logging, redaction, error sanitisation applied uniformly |
| 10 of 12 agentic-AI components implemented | Hostname allow-list — prod excluded by default | Per-call JSONL audit log, Power BI-ingestible |
| Open protocol (MCP) — works with any compliant client | Scope refusal — off-topic Qs refused without touching any system | ContextVars for per-call state (no globals, no leaks across requests) |

---

## How a question becomes an answer — the uniform pipeline

```mermaid
flowchart LR
  C([User question]):::input --> T[Tool wrapper<br/>auto-loaded over MCP]:::http
  T --> L[**@logged_tool decorator**<br/>request_id · timer · secret redaction]:::http
  L --> S{Pre-flight checks}:::decision
  S -->|modify verb<br/>in MQSC| RO[/🚫 BLOCKED<br/>no HTTP call/]:::policy
  S -->|host not in<br/>allow-list| AL[/🚫 RESTRICTED/]:::policy
  S -->|OK| H[/HTTP or local CSV/]:::http
  H -->|success| P[Structured response<br/>table · diagram · text]:::ok
  H -->|exception| E[**safe_error_message**<br/>logs traceback<br/>returns ⚠️ ref id]:::err
  P --> R([Streamed back to user]):::ok
  E --> R

  classDef input fill:#1E3A8A,stroke:#1E3A8A,color:#FFFFFF
  classDef decision fill:#FEF3C7,stroke:#B45309,color:#7C2D12
  classDef http fill:#DBEAFE,stroke:#1D4ED8,color:#1E3A8A
  classDef ok fill:#DCFCE7,stroke:#15803D,color:#14532D
  classDef policy fill:#FEF3C7,stroke:#A16207,color:#713F12
  classDef err fill:#FECACA,stroke:#B91C1C,color:#7F1D1D
```

**Five mechanisms wired in once — applied to *every* call:**

| # | Where | What it enforces |
|---|---|---|
| 1 | `server/safety.py:is_hostname_allowed` | Hostname prefix check against `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES`. |
| 2 | `server/safety.py:is_modification_command` | Blocks `ALTER · DEFINE · DELETE · CLEAR · MOVE · SET · RESET · START · STOP · PURGE · REFRESH · RESOLVE · ARCHIVE · BACKUP` in MQSC. |
| 3 | `server/errors.py:safe_error_message` | Maps exceptions to curated hints, writes traceback to `logs/app-*.log`, returns `⚠️ … (ref <id>)` to the user. |
| 4 | `server/query_log.py:record_endpoint` | Stamps each outbound URL onto the per-call JSONL record. |
| 5 | CSV cache layer in MQ/ACE helpers | Inventory loaded once, cached in-process; restart to refresh. |

> *Engineering discipline: the same five guarantees apply to **every** tool, today and any future tool, because they live in the call wrapper — not the tool body.*

---

## The 13 tools — what the agent can actually do

| **IBM MQ tools (7)** | What it answers |
|---|---|
| `find_mq_object` | "Which QM hosts queue/channel/object X?" — manifest lookup |
| `dspmq` | Queue manager liveness + status |
| `dspmqver` | MQ version + build info |
| `runmqsc` | Free-form MQSC query (modify verbs blocked at boundary) |
| `run_mqsc_for_object` | Targeted MQSC against a named object |
| `get_queue_depth` | Current depth, with auto alias-chain resolution |
| `get_channel_status` | Channel state, transmission queue health |

| **IBM ACE tools (6)** | What it answers |
|---|---|
| `list_ace_nodes` | Inventory of integration nodes |
| `get_ace_node_status` | Live status of one node |
| `list_ace_servers` | Integration servers under a node |
| `list_ace_applications` | Apps deployed on a server |
| `list_ace_message_flows` | Flows inside a server / app |
| `search_ace_local_dump` | Grep BIP error codes in offline manifests |

Every tool is **GET-only** at the protocol layer. Modification verbs cannot be invoked — no destructive tool exists in the registry.

---

## Security — defence in depth (6 independent layers)

| Layer | Control | Backed by |
|---|---|---|
| **1** | **HTTP Basic Auth on SSE endpoint** — unauthenticated calls rejected; `/healthz` carved out for liveness probes only | `MCP_AUTH_USER` / `MCP_AUTH_PASSWORD` + `BasicAuthMiddleware` |
| **2** | **Hostname allow-list** — outbound calls resolve the target hostname and reject anything not on the list. Two lists (MQ + ACE) since infra families differ. Production hosts excluded by default | `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES` |
| **3** | **Read-only enforcement** — `runmqsc` and `run_mqsc_for_object` block all modification verbs at the tool boundary, no HTTP made | `server/safety.py:is_modification_command` |
| **4** | **Scope refusal** — agent refuses off-topic questions (weather, code, finance) *without* invoking any tool | `BOT_DOMAIN` + system prompt |
| **5** | **Tool allow / deny list** — ops can disable any individual tool via config without code changes | `TOOL_ALLOWLIST` / `TOOL_DENYLIST` env |
| **6** | **Error sanitisation + secret redaction** — users see ref-tagged messages; full tracebacks stay in logs; any kwarg matching `password / token / key / secret / auth / pwd / credential` auto-redacted to `[REDACTED]` | `server/errors.py` + `server/query_log.py` |

---

## Agentic AI features — what's actually implemented

| # | Canonical component | This solution | Status |
|---|---|---|---|
| 1 | LLM reasoner | OpenAI GPT-4o via `langchain-openai` | ✅ |
| 2 | Tool registry | 13 MCP tools auto-loaded | ✅ |
| 3 | Tool selector | The LLM itself — guided by `IBM MQ:` / `IBM ACE:` docstring prefixes; **no dispatcher code** | ✅ |
| 4 | Action loop (ReAct) | `langgraph.prebuilt.create_react_agent` | ✅ |
| 5 | Short-term memory | LLM context across messages of one turn | ✅ |
| 6 | Session memory | LangGraph `MemorySaver` keyed by `thread_id` | ✅ |
| 7 | Output formatter | Auto-detects tables / Mermaid / code from tool output shape | ✅ |
| 8 | Guardrails | Scope refusal · tool allow-deny · hostname allow-list · read-only · error sanitisation | ✅ multi-layer |
| 9 | Observability | Per-call JSONL + streaming tool-step events + `/api/health` | ✅ |
| 10 | Streaming I/O | SSE typed events (`token`, `tool_call`, `tool_result`, `final`, `done`) | ✅ |
| 11 | Multi-agent | Single agent — no supervisor pattern | ❌ by design |
| 12 | Human-in-the-loop | Not needed — read-only by construction | ❌ replaced by upstream enforcement |

**10 of 12 implemented; the missing 2 are intentionally not built** (single-agent is sufficient; HITL is replaced by upstream read-only enforcement).

---

## Coding standards & best practices

- **Single-route enforcement.** Safety, logging, and error handling live in shared primitives, not in tool bodies. Adding a new tool inherits all controls automatically.
- **Decorator order is canon.** Every tool is `@mcp.tool()` outer + `@logged_tool` inner — reversed and FastMCP introspection breaks. Convention documented in `CLAUDE.md`.
- **No raw exceptions to the user — ever.** Contract: every caught exception routes through `safe_error_message`. No `str(err)` or `err.response.text` reaches the caller.
- **ContextVars for per-call state.** `_current_query` (set by `@logged_tool`) and `_current_caller` (set by auth middleware) — no globals, no leakage across concurrent requests.
- **Two HTTP clients, one shutdown path.** Singleton `httpx.AsyncClient` for MQ + ACE, both closed via one `aclose_http_client` in the shutdown handler. No ad-hoc clients in tools.
- **Sensitive kwargs auto-redacted.** Any param whose name contains `password / secret / token / auth / pwd / key / credential` becomes `[REDACTED]` in the audit log.
- **Tests live outside `requirements.txt`.** `pytest` + `pytest-asyncio` in dev venv only; production image stays lean.

---

## Governance & observability

- **Two file-based logs, daily-rotated** in `LOG_DIR`:
  - `app-YYYY-MM-DD.log` — plain text, mirrors stderr.
  - `queries-YYYY-MM-DD.jsonl` — one JSON object per tool invocation.
- **Power BI ingestion** via "Get Data → From Folder" on the JSONL directory. No extra ETL.
- **Schema is fixed.** `request_id · caller · tool · args (redacted) · endpoints[] · latency_ms · outcome` — same shape every row, queryable directly.
- **Per-call `request_id`** appears in user-facing error messages (`⚠️ … ref abc123`) — operators can find the full trace in `app-*.log` in one grep.
- **`/api/health`** surfaces resolved prompt source, scope, allow/deny lists, MCP endpoint — runtime configuration is observable from the outside.
- **Allow-list defaults exclude prod** (`lod, loq, lot` prefixes — non-prod environments) — promoting requires an explicit policy change, not a code change.

---

**Speaker note:** The agentic AI is not a demo glued to our platform — it's a governed system whose security, observability, and read-only guarantees are *architectural properties*, not policies that have to be re-enforced by every developer who touches the codebase. That distinction is what makes it credible to run against production diagnostics, and what lets us onboard new tools (or new domains entirely) without lowering the bar.
