# MCP Chatbot

A generic chat UI + agent backend that connects to **any** MCP server over
SSE. Drop in a different `MCP_SSE_URL` and the same UI/backend works
unchanged — there are no MCP-server-specific tool names hardcoded anywhere.

> **Asked why this is "agentic AI"?** See **[AGENTIC_AI.md](AGENTIC_AI.md)** —
> a single doc that maps the canonical agentic-AI components to specific
> files in this repo, with concrete behaviour walk-throughs.

```
Browser → Streamlit UI (:8501)
   │  httpx → /api/chat/stream   (SSE)
   ▼
FastAPI backend (:8001)
   │  LangGraph agent  (OpenAI + MemorySaver)
   │  langchain-mcp-adapters MultiServerMCPClient
   ▼
Any MCP server over SSE
```

## What you get

- **Session-level memory** — the LLM remembers context across turns in the
  same browser tab (in-process, keyed by a `thread_id` stored in
  `localStorage`). A "Reset" button starts a clean thread.
- **Structured output, automatically** — tool results are inspected and
  rendered as:
  - **Tables** when JSON contains a list of objects (`results`, `children`,
    `items`, `nodes`, `servers`, …) or when the text is repeated
    `key:value` lines.
  - **Mermaid flow diagrams** when the LLM emits a fenced ```mermaid block,
    or when a tool result contains one.
  - **Code blocks** for multi-line text output (MQSC, logs, JSON dumps).
  - **Plain text** for single-line results.
- **Scope guardrail** (`BOT_DOMAIN`) — restrict the bot to a domain
  (e.g. "IBM MQ and IBM ACE"); off-topic questions are refused without
  any tool call. Empty value = unrestricted.
- **Customizable header** (`HEADER_TITLE` / `HEADER_SUBTITLE`) — change
  the UI's title bar from the backend `.env`. No frontend rebuild needed.
- **Externalized system prompt** — edit `prompts/system.md` (markdown
  file) instead of touching Python. Loader auto-discovers it; safe
  fallback to an inline template if the file is missing or broken.
- **Tool allow/deny list** (`TOOL_ALLOWLIST` / `TOOL_DENYLIST`) — control
  which MCP tools the agent can see and invoke. Useful for pinning the
  bot to read-only diagnostics or hiding admin tools.
- **No MCP-server coupling** — change `MCP_SSE_URL` to retarget. The
  frontend has *zero* MCP-specific code; backend renderers are
  tool-name-agnostic.

## Frontend

The backend is fronted by a **Streamlit** app in `frontend/`
(`app.py`, `client.py`, `renderers.py`), default port **8501**. It talks
to the backend over HTTP/SSE via `httpx` and stays MCP-server-agnostic —
all header text, scope hint, and tool catalog come from `/api/health`.

## Run order

You need three processes (in three terminals). One-liner once everything is
installed:

```powershell
.\scripts\start-all.ps1            # MCP server + backend + Streamlit UI
.\scripts\start-streamlit.ps1      # same stack, with a -Port switch
.\scripts\start-all.ps1 -SkipMcp   # use this if your MCP server runs elsewhere
.\scripts\start-all.ps1 -CheckOnly # verify prerequisites without launching
.\scripts\stop-all.ps1             # kill everything start-all started
```

The script pre-flights every venv / `.env` and refuses to
launch until each missing piece is fixed (with the fix command printed
inline). The manual steps below are what `start-all.ps1` automates.

### 1. The MCP server you want to chat with

For this repo's bundled MQ/ACE MCP server, from the project root:

```powershell
$env:MCP_TRANSPORT = "sse"
.\.venv\Scripts\python.exe mqacemcpserver\mqacemcpserver.py
# Verify:
curl http://localhost:8000/healthz
```

For a different MCP server, just start it and note its `/sse` URL.

### 2. The chat backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env: set at minimum OPENAI_API_KEY and MCP_SSE_URL.
#           Add MCP_AUTH_USER/PASSWORD if your MCP server uses Basic Auth.
python app.py
# Verify the backend can talk to your MCP server:
curl http://localhost:8001/api/health
```

A successful `/api/health` response now looks like:

```json
{
  "status": "ok",
  "mcp_sse_url": "http://localhost:8000/sse",
  "tool_count": 14,
  "tools": ["dspmq", "list_ace_nodes", "get_cert_details", "..."],
  "bot_domain": "IBM MQ and IBM ACE",
  "header_title": "IBM MQ and ACE assistant",
  "header_subtitle": "",
  "prompt_source": "C:/.../backend/prompts/system.md",
  "tool_allowlist": [],
  "tool_denylist": []
}
```

### 3. The chat UI (Streamlit)

```powershell
cd frontend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # edit if your backend isn't on :8001
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8501
# → http://localhost:8501
```

## Configuration

### Backend (`backend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | Required. OpenAI API key. |
| `OPENAI_MODEL` | `gpt-5.5` | LLM model name. |
| `MCP_SSE_URL` | `http://localhost:8000/sse` | Full SSE URL of any MCP server. |
| `MCP_AUTH_USER`, `MCP_AUTH_PASSWORD` | — | Optional HTTP Basic Auth. |
| `MCP_HEADERS_JSON` | — | Optional JSON object for Bearer tokens / custom headers. Merged on top of Basic Auth. |
| `HEADER_TITLE` | `MCP Chatbot` | UI title bar text. |
| `HEADER_SUBTITLE` | — | Optional subtitle override. When empty, the UI auto-derives one from `BOT_DOMAIN`. |
| `BOT_DOMAIN` | — | When set, the LLM only answers questions about this domain and refuses everything else. Empty = unrestricted. |
| `SYSTEM_PROMPT_FILE` | — | Optional path override for the system prompt template. Default resolution looks for `backend/prompts/system.md`, then falls back to the inline template. |
| `TOOL_ALLOWLIST` | — | Comma-separated tool names; when non-empty, ONLY these tools are exposed to the agent. |
| `TOOL_DENYLIST` | — | Comma-separated tool names; always removed (wins over allowlist for the same name). |
| `CHAT_HOST`, `CHAT_PORT` | `0.0.0.0`, `8001` | FastAPI bind. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:8501` | Comma-separated browser origins allowed to call the backend. (Streamlit calls the backend server-side, so this only matters for direct browser-origin callers.) |

### Frontend (`frontend/.env`)

| Variable | Default | Purpose |
|---|---|---|
| `MCP_BACKEND_URL` | `http://localhost:8001` | URL of the FastAPI backend. |
| `PAGE_TITLE` | backend's `HEADER_TITLE` | Override the browser tab title. |
| `PAGE_ICON` | `💬` | Page icon (any single emoji). |

To point the whole stack at a different MCP server, change `MCP_SSE_URL`
in the **backend** `.env` and restart it — the frontend needs no changes.
All visible UI customisations (header, scope hint, refusal text) flow from
backend `.env` through `/api/health`.

## Customising the assistant

### Edit the system prompt

The active system prompt lives at **`backend/prompts/system.md`**.
It's plain markdown with two required placeholders:

- `{scope_block}` — auto-replaced with the BOT_DOMAIN refusal block when
  `BOT_DOMAIN` is set; empty string otherwise.
- `{tool_catalog}` — auto-replaced with the bullet list of currently
  exposed MCP tools.

Edit the file, add as many lines/sections as you want, then restart only
the backend. The loader uses `str.replace()` (not `.format()`), so stray
`{ ... }` from mermaid examples, JSON snippets, or code samples are safe.

If either required placeholder goes missing, the loader logs a warning
and silently falls back to the inline template — the backend will never
fail to start because of an editing mistake.

To point at a different prompt file:
```ini
SYSTEM_PROMPT_FILE=C:\path\to\my-prompt.md
```

### Scope the bot to a domain

```ini
BOT_DOMAIN=IBM MQ and IBM ACE
```

When set:
- The LLM is instructed to refuse off-topic questions with a fixed
  sentence and to NOT call any tools.
- The UI shows `scope: IBM MQ and IBM ACE` as the header subtitle and
  references the domain in the empty-state placeholder.

Setting `BOT_DOMAIN=` (empty) reverts to fully plug-and-play behaviour.

### Customise the UI header

```ini
HEADER_TITLE=IBM MQ and ACE assistant
HEADER_SUBTITLE=
```

Subtitle precedence: explicit `HEADER_SUBTITLE` value > auto-derived from
`BOT_DOMAIN` > `connected to MCP backend` / `backend unreachable`.

### Restrict which MCP tools the agent sees

```ini
# Only expose these two:
TOOL_ALLOWLIST=dspmq,list_ace_nodes

# Or: expose all tools EXCEPT these:
TOOL_DENYLIST=runmqsc,run_mqsc_for_object
```

Rules:
- Both empty → no filtering (current behaviour).
- Both set → deny wins for any name in both lists.
- Unknown names log a warning at startup (typo guard).
- A tool that has been filtered out is *not* in the agent's tool list —
  the LLM can't invoke it and won't even know it exists.

## Smoke test (against this repo's bundled MQ/ACE MCP server)

After all three processes are up:

| You ask | What you should see |
|---|---|
| "List all ACE nodes." | A **table** with columns `node`, `host`, `nodeport`. |
| "What's the depth of queue PAYMENTS.IN?" | One or more per-QM **code blocks** of MQSC output (depth + alias resolution if any). |
| "Show me message flows on node *X*, server *default*." | Either a **table** of flows or a **Mermaid** diagram (LLM's choice). |
| "Run `DISPLAY QMGR` on QM1." | A **code block** of MQSC output. |
| "Now show its channels." | The agent should remember QM1 from the previous turn — proves session memory. |
| Click the ↺ button, then ask the same follow-up. | Agent has no recollection (proves reset works). |
| "What's the weather in Bangalore today?" (with `BOT_DOMAIN` set) | Fixed refusal: `"I can only help with questions about IBM MQ and IBM ACE…"` — no tool call in the steps panel. |

If the MCP server is stopped mid-conversation, the next turn shows a
sanitised error in the chat — no stack trace.

## Wire protocol (backend ↔ frontend)

`text/event-stream`, one JSON object per `data:` line. Event kinds:

- `{"kind":"token","text":"…"}` — incremental LLM tokens
- `{"kind":"tool_call","name":"…","args":{…},"call_id":"…"}` — agent picked a tool
- `{"kind":"tool_result","name":"…","call_id":"…","block":{…}}` — tool returned, with a structured Block
- `{"kind":"final","blocks":[…]}` — turn closing
- `{"kind":"error","message":"…"}`
- `{"kind":"done"}`

`Block` shapes:
- `{"kind":"text","text":"…","title?":"…"}`
- `{"kind":"markdown","text":"…","title?":"…"}`
- `{"kind":"code","code":"…","lang?":"…","title?":"…"}`
- `{"kind":"mermaid","mermaid":"…","title?":"…"}`
- `{"kind":"table","columns":["…"],"rows":[["…"]],"title?":"…"}`

## File map (after all refinements)

The chatbot stack is two sibling folders at the repo root:

```
backend/                                   ← this folder (FastAPI agent, :8001)
├── README.md                              ← this file
├── AGENTIC_AI.md                          ← agentic-AI component map
├── SAMPLE_QUESTIONS.md                    ← curated demo prompts
├── app.py                                 ← FastAPI: SSE chat / reset / health
├── agent.py                               ← LangGraph create_react_agent +
│                                            MemorySaver + prompt loader +
│                                            SCOPE_BLOCK_TEMPLATE injection
├── mcp_client.py                          ← MultiServerMCPClient + Basic/Bearer
│                                            auth + TOOL_ALLOWLIST/DENYLIST filter
├── renderers.py                           ← generic shape detection (JSON list,
│                                            key:value lines, mermaid fence, code)
├── schemas.py                             ← Block + ChatEvent wire models
├── prompts/
│   └── system.md                          ← editable system prompt template
├── tests/                                 ← run_question_suite.py
├── requirements.txt
└── .env.example

frontend/                                  ← Streamlit UI (:8501)
├── app.py                                 ← Streamlit page, session state,
│                                            streaming loop
├── client.py                              ← httpx client for /api/health,
│                                            /api/chat/reset, /api/chat/stream (SSE)
├── renderers.py                           ← Block renderers (text/markdown/
│                                            table/code/mermaid) + tool-step expander
├── requirements.txt
└── .env.example
```

## Out of scope (v1)

- Persistence beyond process memory (swap `MemorySaver` → `SqliteSaver` later)
- Multi-tenant auth on the chat UI itself
- Tool-output caching
- Production deployment artifacts (Docker, k8s)
- Other guardrails (max iterations, prompt-injection regex, output
  redaction, rate limit) — designed but not built; see plan file in
  `~/.claude/plans/` for the design.
