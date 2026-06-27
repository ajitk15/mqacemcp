"""Wire protocol shared with the frontend.

Every event the backend streams to the UI is one of these models, serialised
as a single-line JSON object on an SSE `data:` line. The frontend dispatches
purely on `kind` — it never sees tool names from a specific MCP server, so
this protocol stays the same regardless of which MCP server is wired up.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Block(BaseModel):
    """A single renderable unit. `kind` decides which UI component renders it."""

    kind: Literal["text", "markdown", "table", "mermaid", "code"]
    text: Optional[str] = None
    columns: Optional[list[str]] = None
    rows: Optional[list[list[str]]] = None
    mermaid: Optional[str] = None
    code: Optional[str] = None
    lang: Optional[str] = None
    title: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    thread_id: str = Field(..., description="Frontend-owned session id")


class ResetRequest(BaseModel):
    thread_id: str


class ConnectRequest(BaseModel):
    """Switch the backend's active MCP server at runtime.

    Only ``url`` is required. ``prompt_file`` is ignored when ``url`` matches a
    known server (the registry's prompt wins); auth args fall back to env.
    """

    url: str
    name: Optional[str] = None
    prompt_file: Optional[str] = None
    auth_user: Optional[str] = None
    auth_password: Optional[str] = None


# ---- Streaming events (one per SSE `data:` line) ---------------------------


class TokenEvent(BaseModel):
    kind: Literal["token"] = "token"
    text: str


class ToolCallEvent(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any] = {}
    call_id: Optional[str] = None


class ToolResultEvent(BaseModel):
    kind: Literal["tool_result"] = "tool_result"
    name: str
    call_id: Optional[str] = None
    block: Block


class FinalEvent(BaseModel):
    kind: Literal["final"] = "final"
    blocks: list[Block] = []


class ErrorEvent(BaseModel):
    kind: Literal["error"] = "error"
    message: str


class DoneEvent(BaseModel):
    kind: Literal["done"] = "done"
