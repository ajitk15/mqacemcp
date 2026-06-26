# Connecting clients to `mqacemcpserver`

How to wire the unified MQ + ACE MCP server into the most common MCP clients.
The server runs in two modes — pick the one that fits the client:

| Mode | When to use | Configured by |
| --- | --- | --- |
| `stdio` | One-machine setups: Claude Desktop running locally, a single VS Code workspace. The client launches the Python process itself. | `MCP_TRANSPORT=stdio` in `.env` |
| `sse` | Hosted endpoint shared by a team / orchestrator. Clients connect over HTTP. | `MCP_TRANSPORT=sse` in `.env` |

Most teams want **SSE**. That's what the rest of this doc assumes unless noted.

---

## Prerequisites

### 1. Server running
```powershell
cd C:\Workspace\hready\mqacemcp
.venv\Scripts\python.exe mqacemcpserver.py
```
You should see `Uvicorn running on http://0.0.0.0:8000` (port and host come from `.env`).

### 2. Liveness probe (no auth)
```powershell
curl http://localhost:8000/healthz
# => {"status":"ok","service":"mqacemcpserver","transport":"sse",...}
```
If this fails, the client config below cannot work. Fix the server first.

### 3. Authorization header
SSE is gated by HTTP Basic Auth when `MCP_AUTH_USER` and `MCP_AUTH_PASSWORD` are
both set in `.env`. Generate the header value once:

```powershell
.venv\Scripts\python.exe scripts\gen_basic_auth.py
# prints:
# Authorization head:  Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ=
```

Every snippet below uses that string verbatim. Re-run the script if the password changes.

---

## Claude Desktop

Config file:

| OS | Path |
| --- | --- |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS   | `~/Library/Application Support/Claude/claude_desktop_config.json` |

### SSE (recommended — server runs as a separate process)

```json
{
  "mcpServers": {
    "mqace": {
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ="
      }
    }
  }
}
```

### stdio (Claude Desktop launches the server itself)

First switch the server to stdio: set `MCP_TRANSPORT=stdio` in `.env`. Then:

```json
{
  "mcpServers": {
    "mqace": {
      "command": "C:\\Workspace\\hready\\mqacemcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Workspace\\hready\\mqacemcp\\mqacemcpserver.py"]
    }
  }
}
```

Restart Claude Desktop after editing. The Settings → Developer panel shows
the connection state and any handshake errors.

---

## Claude Code (CLI)

### SSE

```powershell
claude mcp add `
  --transport sse `
  mqace http://localhost:8000/sse `
  --header "Authorization: Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ="
```

### stdio

```powershell
claude mcp add mqace -- `
  "C:\Workspace\hready\mqacemcp\.venv\Scripts\python.exe" `
  "C:\Workspace\hready\mqacemcp\mqacemcpserver.py"
```

### Verify

```powershell
claude mcp list                # should show "mqace" with status connected
claude mcp get mqace           # full details for the entry
claude mcp remove mqace        # if you need to undo
```

Inside a Claude Code session, `/mcp` shows live status of every configured server.

---

## VS Code

VS Code has multiple MCP-aware extensions. The two most common patterns:

### GitHub Copilot agent mode (`.vscode/mcp.json` per project)

Create `.vscode/mcp.json` at the workspace root:

```json
{
  "servers": {
    "mqace": {
      "type": "sse",
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ="
      }
    }
  }
}
```

Open the Copilot Chat panel, switch to **Agent** mode, and the `mqace` server's
tools appear in the tool picker. Copilot prompts before running each tool the
first time.

### Claude Code extension for VS Code

Install the official Claude Code extension. It uses the same `~/.claude.json`
that the CLI writes to, so any `claude mcp add …` you ran above is picked up
automatically. No separate config needed.

### Other extensions (Cline, Continue, etc.)

Each has its own config UI; the values you need are always the same three:
- **Transport / type**: `sse`
- **URL**: `http://localhost:8000/sse`
- **Headers**: `Authorization: Basic <your base64>`

---

## Cursor

Project-scoped: `.cursor/mcp.json` at the workspace root.
User-scoped (all workspaces): `~/.cursor/mcp.json`.

```json
{
  "mcpServers": {
    "mqace": {
      "url": "http://localhost:8000/sse",
      "headers": {
        "Authorization": "Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ="
      }
    }
  }
}
```

---

## MCP Inspector (debugging)

Best tool to confirm tools register and to call them by hand without an LLM in the loop.

### Against the running SSE endpoint

```powershell
npx @modelcontextprotocol/inspector
```
Then in the UI:
- **Transport**: `SSE`
- **URL**: `http://localhost:8000/sse`
- **Custom Headers** → add: `Authorization: Basic bWNwYWRtaW46TXlSZWFsUGFzc3dvcmQ=`
- Click **Connect**.

You'll see all 14 tools in the left panel — call any of them with arbitrary
arguments and inspect the response.

### As a stdio launcher (no server needed beforehand)

```powershell
npx @modelcontextprotocol/inspector .venv\Scripts\python.exe mqacemcpserver.py
```
The Inspector starts the server itself, no `.env` `MCP_TRANSPORT` change
required for this one-shot usage — but ensure your shell env exposes the
MQ/ACE creds the server needs.

---

## Web chat UI (bundled, `backend/` + `frontend/`)

A standalone Streamlit + FastAPI chat UI lives in `backend/` + `frontend/`. It is itself an
MCP client — it connects to the SSE endpoint just like Claude Desktop or
the Inspector would. Use it when you want a brandable web chat surface for
operators rather than a desktop client.

```powershell
.\scripts\start-all.ps1   # MCP server + chat backend + Streamlit UI + dashboard in 4 windows
```

The chat backend is configured via `backend/.env`. The two values
that connect it to the MCP server:

```ini
MCP_SSE_URL=http://localhost:8000/sse
MCP_AUTH_USER=mcpadmin
MCP_AUTH_PASSWORD=MyRealPassword
```

Other notable knobs (full list in `backend/README.md`):
- `BOT_DOMAIN` — restrict the bot to an explicit topic (e.g. `IBM MQ and
  IBM ACE`). Off-topic questions are refused without invoking any tool.
- `HEADER_TITLE` / `HEADER_SUBTITLE` — UI title bar customisation.
- `SYSTEM_PROMPT_FILE` — point at a custom system prompt markdown file.
- `TOOL_ALLOWLIST` / `TOOL_DENYLIST` — limit which MCP tools the agent
  is allowed to invoke.

The frontend is MCP-server-agnostic — it never sees tool names directly.
Verify connectivity:

```powershell
curl http://localhost:8001/api/health
# tool_count, bot_domain, prompt_source, etc. should match your .env
```

---

## Programmatic MCP client (Python SDK)

For the central team's orchestrator, or for integration tests:

```python
import asyncio
import base64
from mcp import ClientSession
from mcp.client.sse import sse_client

URL = "http://localhost:8000/sse"
TOKEN = base64.b64encode(b"mcpadmin:MyRealPassword").decode()
HEADERS = {"Authorization": f"Basic {TOKEN}"}

async def main():
    async with sse_client(URL, headers=HEADERS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])

            result = await session.call_tool(
                "find_mq_object", {"search_string": "QL.IN.APP1"}
            )
            print(result.content[0].text)

asyncio.run(main())
```

Same auth pattern works for any MCP-SDK language binding (TypeScript, etc.) —
pass headers when constructing the SSE transport.

---

## Endpoint reference

| Path | Auth | Purpose |
| --- | --- | --- |
| `GET /sse` | Basic Auth (when configured) | The MCP SSE endpoint — every client points here |
| `GET /healthz` | None — auth is bypassed by design | Liveness/readiness for load balancers and monitors; also reports each CSV manifest's freshness under `"manifests"` (`rows`, `file_mtime`, `loaded_at`, `stale`) |

Headers expected on `/sse`:
```
Authorization: Basic <base64(MCP_AUTH_USER:MCP_AUTH_PASSWORD)>
Accept: text/event-stream
```
The MCP SDK and every client above set `Accept` automatically; you only ever
configure `Authorization`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `EventSource error: request to … failed, reason:` (empty) | Server not running, or wrong port | `curl http://localhost:8000/healthz` — if it fails too, start the server |
| `401 Unauthorized` from the client | Wrong / missing `Authorization` header | Re-run `scripts/gen_basic_auth.py`, paste exactly (no extra whitespace) |
| Client connects but no tools appear | Server started before you fixed `.env` (e.g. paths to CSVs) | Stop & restart the server; check `logs/app-*.log` |
| Auth header looks like `Basic …<password>…` | The literal placeholder `<password>` was pasted | Substitute the real password before encoding |
| `Hostname '…' is not in the allowed list` returned by a tool | The host you're querying isn't in `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES` | Either add the prefix in `.env` (and restart) or pick a host in the allow-list |
| `🚫 Modification requests are not permitted` | You sent an MQSC command that mutates state (`ALTER`, `DEFINE`, …) | Use a `DISPLAY` variant or open a ServiceNow ticket per the message |
| Inspector log says `SSE transport is deprecated and has been replaced by StreamableHttp` | Informational only | Ignore — current server uses SSE intentionally and works fine |

When you're not sure what happened, every tool call lands in
`logs/queries-YYYY-MM-DD.jsonl` with a `request_id`, and the matching
exception (if any) is in `logs/app-YYYY-MM-DD.log` with the same id.
Quote that id when you ask for help.
