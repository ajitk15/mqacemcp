# Streamlit frontend

The chat UI for the FastAPI backend. A Streamlit app that streams from the
backend over HTTP/SSE and stays MCP-server-agnostic — nothing in the UI is
hardcoded to a particular MCP server.

```
Streamlit UI (:8501)
   │  httpx → /api/chat/stream (SSE)
   ▼
FastAPI backend (:8001)            ← unchanged
   ▼
Any MCP server over SSE            ← unchanged
```

## What you get

- **Streaming tokens** — assistant text streams in as it's produced.
- **Tool steps** — each tool invocation is shown inline with name + args
  in a collapsible panel; results are rendered structurally (table /
  code / mermaid / markdown / text) once they arrive.
- **Session memory** — each Streamlit session has a `thread_id` so the
  backend agent remembers prior turns. "New conversation" rotates the
  thread and clears the in-process memory on the backend.
- **Dynamic header** — title + subtitle come from `/api/health`
  (`HEADER_TITLE`, `HEADER_SUBTITLE`, `BOT_DOMAIN`). Nothing in the UI is
  hardcoded to a particular MCP server.
- **Scope guardrail awareness** — when the backend reports a
  `BOT_DOMAIN`, the empty-state hint and input placeholder reflect it.
- **Sidebar diagnostics** — connection status, tool count, the loaded
  tool catalog, allow/deny filters, prompt source.

## One-time setup

```powershell
cd frontend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env       # then edit if your backend isn't on :8001
```

## Run

The chatbot backend must already be running (see `../backend/README.md` —
the Streamlit UI does NOT replace the backend, only fronts it).

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

The app opens at <http://localhost:8501>.

To pick a different port:

```powershell
streamlit run app.py --server.port 8502
```

## Configuration

All knobs live in `.env` (loaded at startup) or as environment variables:

| Var | Default | Purpose |
| --- | --- | --- |
| `MCP_BACKEND_URL` | `http://localhost:8001` | Where the FastAPI backend lives. |
| `PAGE_TITLE` | `` (uses backend's `HEADER_TITLE`) | Override the browser tab title. |
| `PAGE_ICON` | `💬` | Page icon (any single emoji). |

Everything else (header text, scope hint, available tools, filters,
prompt source) is driven by the backend's `/api/health` response. So
changing `HEADER_TITLE` / `HEADER_SUBTITLE` / `BOT_DOMAIN` /
`TOOL_ALLOWLIST` in the backend `.env` flows through to this UI without
touching code here.

## Architecture

```
app.py          — Streamlit page, session state, streaming loop
client.py       — httpx client for /api/health, /api/chat/reset,
                  /api/chat/stream (SSE)
renderers.py    — Block renderers (text/markdown/table/code/mermaid)
                  + tool-step expander
```

### Why a separate file for renderers?

The wire protocol (`Block` shapes) is defined in
`../backend/schemas.py`. Keeping renderers isolated from `app.py`
makes it trivial to add a new `kind` (just update both ends —
`schemas.py` and `renderers.py`).

### Mermaid

Diagrams render in a sandboxed iframe via `streamlit.components.v1.html`,
loading mermaid 10 from a CDN — no extra Python deps.

## Caveats

- **Streamlit reruns the whole script on every interaction.** The
  streaming code re-uses the existing assistant turn record from
  `st.session_state` so prior tool steps are preserved.
- **Per-session memory only.** Closing the browser tab issues a new
  `thread_id` on next load, so the backend agent starts a fresh thread.
- **No auth on the Streamlit hop.** If you put the backend behind auth,
  put a reverse proxy in front of Streamlit too — this UI doesn't
  forward credentials.
