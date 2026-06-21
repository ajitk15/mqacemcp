"""User-safe error formatting.

The contract: a tool's return value never contains a raw exception, response
body, URL, or stack-trace fragment. Full detail lands in the application log
tagged with the same `request_id` that the per-call query log emits, so
support can correlate a user complaint to the underlying failure.

Usage from any helper that catches an outbound exception:

    from server.errors import safe_error_message
    try:
        ...
    except Exception as err:
        return safe_error_message(err, hint="MQ REST API call failed")
"""
from __future__ import annotations

from typing import Any

import httpx

from server.logger import get_logger
from server.query_log import _current_query

logger = get_logger("mqacemcpserver.errors")


# ---------------------------------------------------------------------------
# Status-code / exception → curated hint mapping
# ---------------------------------------------------------------------------
_STATUS_HINTS: dict[int, str] = {
    401: "Authentication failed",
    403: "Access denied — the configured user is not permitted",
    404: "Endpoint not found",
    429: "Upstream is rate-limiting requests",
    500: "Upstream server error",
    502: "Upstream gateway error",
    503: "Upstream service is unavailable",
    504: "Upstream gateway timed out",
}


def _hint_from_exception(err: Exception, default: str) -> str:
    """Pick a curated hint that does not echo the raw exception text."""
    if hasattr(err, "response") and hasattr(err.response, "status_code"):
        code = err.response.status_code
        if code in _STATUS_HINTS:
            return _STATUS_HINTS[code]
        return f"Upstream returned an unexpected status (HTTP {code})"

    err_str = str(err).lower()
    if "timeout" in err_str or "timed out" in err_str:
        return "Connection timed out"
    if "ssl" in err_str or "certificate" in err_str:
        return "TLS / certificate error"
    if "refused" in err_str or "connect" in err_str:
        return "Cannot connect to the upstream service"
    if isinstance(err, httpx.HTTPError):
        return "Network error talking to the upstream service"
    return default


def _current_request_id() -> str | None:
    q = _current_query.get()
    if q is None:
        return None
    return q.get("request_id")


def safe_error_message(
    err: Exception,
    *,
    hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Return a user-safe message and write the raw error to the app log.

    The returned string is a single short sentence ending in `(ref <id>)` so
    the user can quote the id to support. The raw exception (with traceback)
    only goes to the application log.
    """
    request_id = _current_request_id() or "n/a"
    chosen_hint = _hint_from_exception(err, hint or "Upstream call failed")

    log_extra = {"request_id": request_id}
    if extra:
        log_extra.update(extra)

    logger.exception(
        "%s [request_id=%s, extra=%s]", chosen_hint, request_id, extra or {}
    )

    return f"⚠️ {chosen_hint} (ref {request_id})"
