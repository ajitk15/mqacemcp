"""HTTP client to the chatbot FastAPI backend.

Three endpoints, mirrored from the Next.js client (`chatbot/frontend/lib`):
  GET  /api/health         -> backend metadata used to drive header + UX
  POST /api/chat/reset     -> drop a thread's in-memory state
  POST /api/chat/stream    -> server-sent events of typed chat events

This module is MCP-server-agnostic; it only speaks the wire protocol
defined in `chatbot/backend/schemas.py`.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterator

import httpx


def _backend_url() -> str:
    return os.getenv("MCP_BACKEND_URL", "http://localhost:8001").rstrip("/")


# Read indefinitely while streaming (the backend keeps the SSE connection open
# until the agent emits `done`); short connect/write timeouts catch dead backends.
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0)
_SHORT_TIMEOUT = httpx.Timeout(10.0)


def get_health() -> Dict[str, Any]:
    """Fetch backend metadata. Returns {} on any failure (caller decides fallbacks)."""
    try:
        with httpx.Client(timeout=_SHORT_TIMEOUT) as client:
            response = client.get(f"{_backend_url()}/api/health")
            response.raise_for_status()
            return response.json()
    except Exception:
        return {}


def reset_thread(thread_id: str) -> bool:
    try:
        with httpx.Client(timeout=_SHORT_TIMEOUT) as client:
            response = client.post(
                f"{_backend_url()}/api/chat/reset",
                json={"thread_id": thread_id},
            )
            return response.status_code == 200
    except Exception:
        return False


def stream_chat(message: str, thread_id: str) -> Iterator[Dict[str, Any]]:
    """Yield decoded SSE event dicts from the backend.

    Each yielded dict has a `kind` field: token | tool_call | tool_result |
    final | error | done. On HTTP failure, an error + done pair is yielded so
    callers can rely on the done sentinel to terminate.
    """
    payload = {"message": message, "thread_id": thread_id}
    try:
        with httpx.Client(timeout=_STREAM_TIMEOUT) as client:
            with client.stream(
                "POST",
                f"{_backend_url()}/api/chat/stream",
                json=payload,
            ) as response:
                if response.status_code != 200:
                    yield {"kind": "error", "message": f"Backend HTTP {response.status_code}"}
                    yield {"kind": "done"}
                    return

                buffer = ""
                for chunk in response.iter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        raw, buffer = buffer.split("\n\n", 1)
                        data_line = next(
                            (line for line in raw.splitlines() if line.startswith("data:")),
                            None,
                        )
                        if not data_line:
                            continue
                        body = data_line[5:].strip()
                        if not body:
                            continue
                        try:
                            yield json.loads(body)
                        except json.JSONDecodeError:
                            continue
    except httpx.RequestError as err:
        yield {"kind": "error", "message": f"Backend unreachable: {err.__class__.__name__}"}
        yield {"kind": "done"}
