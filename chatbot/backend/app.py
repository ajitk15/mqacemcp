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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("Loading MCP tools at startup...")
    try:
        tools = await mcp_client.load_tools()
    except Exception as err:  # noqa: BLE001
        log.exception("Failed to load MCP tools: %s", err)
        tools = []
    agent, checkpointer = build_agent(tools)
    _state["tools"] = tools
    _state["agent"] = agent
    _state["checkpointer"] = checkpointer
    log.info("Backend ready (tools=%d)", len(tools))
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
    return JSONResponse(
        {
            "status": "ok",
            "mcp_sse_url": os.getenv("MCP_SSE_URL", ""),
            "tool_count": len(tools),
            "tools": [t.name for t in tools],
            "bot_domain": os.getenv("BOT_DOMAIN", "").strip(),
            "header_title": os.getenv("HEADER_TITLE", "").strip() or "MCP Chatbot",
            "header_subtitle": os.getenv("HEADER_SUBTITLE", "").strip(),
            "prompt_source": get_prompt_source(),
            "tool_allowlist": allow,
            "tool_denylist": deny,
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
