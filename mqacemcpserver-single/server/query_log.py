"""Per-tool-call query log for Power BI ingestion.

Writes one JSON object per line to `logs/queries-YYYY-MM-DD.jsonl`.
The schema is intentionally flat-ish so Power BI's "From Folder" + JSON
parser can ingest the whole rotated set with minimal transforms.

Schema (one record per MCP tool invocation):
    ts            ISO 8601 UTC timestamp (string)
    request_id    UUID hex (string)
    transport     "stdio" or "sse" (string)
    caller        SSE Basic Auth username, or null
    tool          name of the MCP tool (string)
    args          sanitized kwargs (object); secrets are replaced with "[REDACTED]"
    endpoints     ordered list of remote URLs the tool actually hit (array of strings)
    outcome       "success" or "error" (string)
    error         error message string, or null on success
    latency_ms    end-to-end wall time in milliseconds (integer)
    response_bytes  byte length of the string return value, or null (integer | null)
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from server.config import LOG_DIR, MCP_TRANSPORT, QUERY_LOG_ENABLED
from server.logger import get_logger

logger = get_logger("mqacemcpserver.query")

_SECRET_HINTS = ("password", "secret", "token", "auth", "pwd", "key", "credential")


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _SECRET_HINTS)


def sanitize_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of kwargs with secret-looking values masked."""
    out: dict[str, Any] = {}
    for k, v in kwargs.items():
        if _is_secret_key(k):
            out[k] = "[REDACTED]"
        else:
            try:
                json.dumps(v)
                out[k] = v
            except TypeError:
                out[k] = repr(v)
    return out


_current_query: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_query", default=None
)
_current_caller: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_caller", default=None
)


def record_endpoint(url: str) -> None:
    """Stamp a remote URL onto the in-flight query record. No-op outside a tool call."""
    q = _current_query.get()
    if q is not None:
        q["endpoints"].append(url)


def set_current_caller(username: str | None) -> contextvars.Token:
    """Set the authenticated caller for the current task. Returns a reset token."""
    return _current_caller.set(username)


def reset_current_caller(token: contextvars.Token) -> None:
    _current_caller.reset(token)


class _QueryLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fh = None
        self._date: str | None = None

    def _path_for_today(self) -> tuple[str, str]:
        date = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(str(LOG_DIR), f"queries-{date}.jsonl"), date

    def _ensure_open(self) -> None:
        path, date = self._path_for_today()
        if self._fh is None or self._date != date:
            self.close_locked()
            try:
                self._fh = open(path, "a", encoding="utf-8")
                self._date = date
            except OSError as e:
                logger.warning("Could not open query log %s: %s", path, e)
                self._fh = None
                self._date = None

    def write(self, record: dict) -> None:
        if not QUERY_LOG_ENABLED:
            return
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._ensure_open()
            if self._fh is None:
                return
            try:
                self._fh.write(line + "\n")
                self._fh.flush()
            except OSError as e:
                logger.warning("Failed to write to query log: %s", e)

    def close_locked(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self._date = None

    def close(self) -> None:
        with self._lock:
            self.close_locked()


_QUERY_LOG = _QueryLog()


def close() -> None:
    _QUERY_LOG.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _build_record(
    tool: str,
    request_id: str,
    args: dict,
    started: float,
    outcome: str,
    error: str | None,
    result: Any,
    endpoints: list[str],
) -> dict:
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response_bytes: int | None = None
    if isinstance(result, (str, bytes, bytearray)):
        response_bytes = len(result)
    return {
        "ts": _now_iso(),
        "request_id": request_id,
        "transport": MCP_TRANSPORT,
        "caller": _current_caller.get(),
        "tool": tool,
        "args": args,
        "endpoints": endpoints,
        "outcome": outcome,
        "error": error,
        "latency_ms": elapsed_ms,
        "response_bytes": response_bytes,
    }


def _bind_args(fn: Callable, args: tuple, kwargs: dict) -> dict:
    """Bind positional + keyword args to parameter names so the log captures both."""
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except TypeError:
        return dict(kwargs)


def logged_tool(fn: Callable) -> Callable:
    """Wrap an MCP tool to emit one JSONL record per invocation.

    Preserves the function's signature and docstring (FastMCP relies on
    `inspect.signature` and the docstring for tool metadata, both of which
    follow `__wrapped__` set by `functools.wraps`).
    """
    is_coro = inspect.iscoroutinefunction(fn)

    if is_coro:

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            request_id = uuid.uuid4().hex
            sanitized = sanitize_args(_bind_args(fn, args, kwargs))
            record_in_flight = {
                "request_id": request_id,
                "tool": fn.__name__,
                "endpoints": [],
            }
            token = _current_query.set(record_in_flight)
            started = time.perf_counter()
            outcome = "success"
            error_msg: str | None = None
            result: Any = None
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception as exc:
                outcome = "error"
                error_msg = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                try:
                    _QUERY_LOG.write(
                        _build_record(
                            tool=fn.__name__,
                            request_id=request_id,
                            args=sanitized,
                            started=started,
                            outcome=outcome,
                            error=error_msg,
                            result=result,
                            endpoints=list(record_in_flight["endpoints"]),
                        )
                    )
                finally:
                    _current_query.reset(token)

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        request_id = uuid.uuid4().hex
        sanitized = sanitize_args(_bind_args(fn, args, kwargs))
        record_in_flight = {
            "request_id": request_id,
            "tool": fn.__name__,
            "endpoints": [],
        }
        token = _current_query.set(record_in_flight)
        started = time.perf_counter()
        outcome = "success"
        error_msg: str | None = None
        result: Any = None
        try:
            result = fn(*args, **kwargs)
            return result
        except Exception as exc:
            outcome = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                _QUERY_LOG.write(
                    _build_record(
                        tool=fn.__name__,
                        request_id=request_id,
                        args=sanitized,
                        started=started,
                        outcome=outcome,
                        error=error_msg,
                        result=result,
                        endpoints=list(record_in_flight["endpoints"]),
                    )
                )
            finally:
                _current_query.reset(token)

    return sync_wrapper
