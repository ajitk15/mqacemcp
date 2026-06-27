"""FastAPI service: SSE chat stream + reset + health.

Endpoints:
  POST /api/chat/stream   -> text/event-stream of typed JSON events
  POST /api/chat/reset    -> clears a thread's in-process memory
  GET  /api/health        -> reports MCP connectivity and loaded tool count

Frontend is generic and renders purely off the event protocol defined in
`schemas.py` — there are no MCP-server-specific affordances on this hop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

import mcp_client
from agent import build_agent, get_prompt_source
from renderers import render
from schemas import (
    Block,
    ChatRequest,
    ConnectRequest,
    DoneEvent,
    ErrorEvent,
    FinalEvent,
    ResetRequest,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("chatbot.app")


_state: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# MCP server registry + runtime activation
# ---------------------------------------------------------------------------


def _known_servers() -> list[dict[str, Any]]:
    """Parse MCP_SERVERS_JSON into a list of server dicts.

    Falls back to a single entry derived from MCP_SSE_URL when the var is
    unset or malformed, so the backend always has at least one server.
    """
    raw = os.getenv("MCP_SERVERS_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            servers = [s for s in parsed if isinstance(s, dict) and s.get("url")]
            if servers:
                return servers
        except Exception:  # noqa: BLE001
            log.warning("MCP_SERVERS_JSON is not valid JSON; falling back to MCP_SSE_URL.")
    url = os.getenv("MCP_SSE_URL", "").strip()
    return [{"name": url or "mcp", "url": url, "default": True}] if url else []


def _default_server() -> dict[str, Any]:
    """Return the server activated at startup: the default flag, else the first."""
    servers = _known_servers()
    if not servers:
        return {"name": "mcp", "url": os.getenv("MCP_SSE_URL", "").strip()}
    for s in servers:
        if s.get("default"):
            return s
    return servers[0]


def _find_server(url: str) -> dict[str, Any] | None:
    """Look up a known server by exact URL match."""
    url = (url or "").strip()
    return next((s for s in _known_servers() if (s.get("url") or "").strip() == url), None)


async def _activate(
    url: str,
    name: str | None = None,
    prompt_file: str | None = None,
    auth_user: str | None = None,
    auth_pwd: str | None = None,
) -> list[Any]:
    """Connect to ``url``, (re)build the agent, and store it in _state.

    Raises on failure so callers can surface a clean error; _state is only
    mutated once the new agent is successfully built.
    """
    tools = await mcp_client.load_tools(url, auth_user, auth_pwd)
    agent, checkpointer = build_agent(tools, prompt_file=prompt_file)
    _state["tools"] = tools
    _state["agent"] = agent
    _state["checkpointer"] = checkpointer
    _state["active_url"] = url
    _state["active_name"] = name or url
    _state["active_prompt"] = prompt_file
    log.info("Activated MCP server %s (%s) with %d tool(s)", name or url, url, len(tools))
    return tools


@asynccontextmanager
async def lifespan(_app: FastAPI):
    default = _default_server()
    log.info("Loading MCP tools at startup from %s...", default.get("url"))
    try:
        await _activate(
            url=default.get("url", ""),
            name=default.get("name"),
            prompt_file=default.get("prompt_file"),
        )
    except Exception as err:  # noqa: BLE001
        log.exception("Failed to load MCP tools at startup: %s", err)
        # Build an empty agent so the service still answers /health and can be
        # pointed at a working server via /api/mcp/connect.
        agent, checkpointer = build_agent([], prompt_file=default.get("prompt_file"))
        _state.update(
            tools=[],
            agent=agent,
            checkpointer=checkpointer,
            active_url=default.get("url", ""),
            active_name=default.get("name") or default.get("url", ""),
            active_prompt=default.get("prompt_file"),
        )
    log.info("Backend ready (tools=%d)", len(_state.get("tools") or []))
    try:
        yield
    finally:
        log.info("Shutting down backend.")


app = FastAPI(title="MCP Chatbot Backend", lifespan=lifespan)

_origins = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:8501").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> JSONResponse:
    tools = _state.get("tools") or []
    allow, deny = mcp_client.get_tool_filters()
    active_url = _state.get("active_url") or os.getenv("MCP_SSE_URL", "")
    return JSONResponse(
        {
            "status": "ok",
            "mcp_sse_url": active_url,
            "mcp_server_name": _state.get("active_name") or active_url,
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
            "bot_domain": os.getenv("BOT_DOMAIN", "").strip(),
            "header_title": os.getenv("HEADER_TITLE", "").strip() or "MCP Chatbot",
            "header_subtitle": os.getenv("HEADER_SUBTITLE", "").strip(),
            "prompt_source": get_prompt_source(_state.get("active_prompt")),
            "tool_allowlist": allow,
            "tool_denylist": deny,
        }
    )


@app.get("/api/mcp/servers")
async def mcp_servers() -> JSONResponse:
    """List selectable MCP servers and which one is currently active."""
    servers = [{"name": s.get("name") or s.get("url"), "url": s.get("url")} for s in _known_servers()]
    active_url = _state.get("active_url") or os.getenv("MCP_SSE_URL", "")
    return JSONResponse(
        {
            "servers": servers,
            "active_url": active_url,
            "active_name": _state.get("active_name") or active_url,
        }
    )


@app.post("/api/mcp/connect")
async def mcp_connect(req: ConnectRequest) -> JSONResponse:
    """Switch the active MCP server. Reloads tools and rebuilds the agent.

    A known server's registered prompt_file always wins; for an ad-hoc custom
    URL the request's prompt_file (or none) is used. Connection failures return
    a 200 with status="error" so the UI can show the message without the
    backend 500-ing (the previously active agent stays in place).
    """
    url = (req.url or "").strip()
    if not url:
        return JSONResponse({"status": "error", "message": "A server URL is required."})

    known = _find_server(url)
    name = (known or {}).get("name") or req.name or url
    prompt_file = (known or {}).get("prompt_file") if known else req.prompt_file

    try:
        tools = await _activate(
            url=url,
            name=name,
            prompt_file=prompt_file,
            auth_user=req.auth_user,
            auth_pwd=req.auth_password,
        )
    except Exception as err:  # noqa: BLE001
        log.exception("Failed to connect to MCP server %s: %s", url, err)
        return JSONResponse(
            {
                "status": "error",
                "message": f"Could not connect to {url}: {err.__class__.__name__}",
                "active_url": _state.get("active_url", ""),
                "active_name": _state.get("active_name", ""),
            }
        )

    return JSONResponse(
        {
            "status": "ok",
            "active_url": url,
            "active_name": name,
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
        }
    )


@app.post("/api/chat/reset")
async def reset(req: ResetRequest) -> JSONResponse:
    checkpointer = _state.get("checkpointer")
    if checkpointer is None:
        raise HTTPException(503, "Backend not initialised")
    # MemorySaver stores per-thread state in an in-memory dict. Drop it.
    storage = getattr(checkpointer, "storage", None)
    if isinstance(storage, dict):
        # Keys are (thread_id, checkpoint_ns) tuples in recent LangGraph;
        # older versions key by thread_id directly. Handle both.
        removed = 0
        for key in list(storage.keys()):
            if (isinstance(key, tuple) and key and key[0] == req.thread_id) or key == req.thread_id:
                storage.pop(key, None)
                removed += 1
        log.info("Reset thread %s (%d entries cleared)", req.thread_id, removed)
    return JSONResponse({"status": "ok", "thread_id": req.thread_id})


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    agent = _state.get("agent")
    if agent is None:
        raise HTTPException(503, "Backend not initialised")
    return StreamingResponse(
        _run(agent, req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Streaming the agent
# ---------------------------------------------------------------------------


def _sse(event_obj: Any) -> bytes:
    payload = event_obj.model_dump(exclude_none=True) if hasattr(event_obj, "model_dump") else event_obj
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")


async def _run(agent: Any, req: ChatRequest) -> AsyncIterator[bytes]:
    config = {"configurable": {"thread_id": req.thread_id}}
    inputs = {"messages": [HumanMessage(content=req.message)]}

    final_text_chunks: list[str] = []

    try:
        async for mode, payload in agent.astream(
            inputs, config=config, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                msg, _meta = payload
                # Token deltas from the LLM
                if isinstance(msg, AIMessageChunk) and msg.content:
                    text = _content_to_text(msg.content)
                    if text:
                        final_text_chunks.append(text)
                        yield _sse(TokenEvent(text=text))
                continue

            if mode == "updates":
                # `payload` is {node_name: state_update}
                for node_name, state in payload.items():
                    messages = (state or {}).get("messages", [])
                    for message in messages:
                        # New tool calls from the agent
                        if isinstance(message, AIMessage):
                            for tc in (message.tool_calls or []):
                                yield _sse(
                                    ToolCallEvent(
                                        name=tc.get("name", ""),
                                        args=tc.get("args", {}) or {},
                                        call_id=tc.get("id"),
                                    )
                                )
                        # Tool results
                        elif isinstance(message, ToolMessage):
                            block = _render_safely(message.name or "tool", message.content)
                            yield _sse(
                                ToolResultEvent(
                                    name=message.name or "tool",
                                    call_id=message.tool_call_id,
                                    block=block,
                                )
                            )
    except asyncio.CancelledError:
        raise
    except Exception as err:  # noqa: BLE001
        log.exception("Chat stream failed: %s", err)
        yield _sse(ErrorEvent(message=f"Backend error: {err.__class__.__name__}"))
        yield _sse(DoneEvent())
        return

    # Emit a `final` event so the UI can finalise rendering. The narrative
    # itself was already streamed via token events; this is a structural cue.
    yield _sse(FinalEvent(blocks=[]))
    yield _sse(DoneEvent())


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return ""


def _render_safely(tool_name: str, raw: Any) -> Block:
    try:
        return render(tool_name, raw)
    except Exception as err:  # noqa: BLE001
        log.exception("Renderer failed for %s: %s", tool_name, err)
        return Block(kind="text", text=str(raw))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("CHAT_HOST", "0.0.0.0"),
        port=int(os.getenv("CHAT_PORT", "8001")),
        reload=False,
    )
