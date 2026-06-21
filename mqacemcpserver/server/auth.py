"""ASGI Basic Auth middleware for the MCP SSE endpoint."""
from __future__ import annotations

import base64
import hmac

from server.query_log import reset_current_caller, set_current_caller

# Paths that bypass Basic Auth (ops monitoring). Keep this list small.
_AUTH_BYPASS_PATHS = frozenset({"/healthz"})


class BasicAuthMiddleware:
    """Enforces HTTP Basic Authentication on every HTTP request.

    Wrap the FastMCP SSE app with this middleware to gate the endpoint on a
    static username/password pair (typically loaded from environment).
    Lifespan and other non-HTTP scopes pass through untouched.

    On a successful auth check the authenticated username is stashed in the
    `_current_caller` ContextVar (see server.query_log) so per-tool query log
    records can attribute calls to a caller.
    """

    def __init__(self, app, username: str, password: str) -> None:
        self.app = app
        self.username = username
        self.password = password

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # Skip auth entirely for whitelisted ops paths (e.g. /healthz).
            if scope.get("path") in _AUTH_BYPASS_PATHS:
                await self.app(scope, receive, send)
                return

            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            ok, user = self._check_auth(auth_header)
            if not ok:
                await self._send_401(send)
                return
            token = set_current_caller(user)
            try:
                await self.app(scope, receive, send)
            finally:
                reset_current_caller(token)
            return
        await self.app(scope, receive, send)

    def _check_auth(self, auth_header: str) -> tuple[bool, str | None]:
        if not auth_header.startswith("Basic "):
            return False, None
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            user, pwd = decoded.split(":", 1)
        except Exception:
            return False, None
        ok = hmac.compare_digest(user, self.username) and hmac.compare_digest(
            pwd, self.password
        )
        return (ok, user if ok else None)

    @staticmethod
    async def _send_401(send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"www-authenticate", b'Basic realm="MCP Server"'],
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"Unauthorized"})
