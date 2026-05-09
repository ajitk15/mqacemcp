"""Tests for the user-safe error sanitiser."""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from server.errors import safe_error_message
from server.query_log import logged_tool


def test_safe_error_message_never_echoes_raw_exception():
    @logged_tool
    def raises_with_internal_details() -> str:
        try:
            raise httpx.ConnectError("connect error to 192.168.1.50:9443")
        except Exception as e:
            return safe_error_message(e, hint="MQ REST API call failed")

    out = raises_with_internal_details()
    assert "192.168.1.50" not in out
    assert "ConnectError" not in out
    assert out.startswith("⚠️")
    assert "ref " in out


def test_safe_error_message_uses_status_code_hint_for_401():
    @logged_tool
    def four_oh_one() -> str:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 401
        err = httpx.HTTPStatusError("401", request=MagicMock(), response=resp)
        return safe_error_message(err, hint="ignored fallback")

    out = four_oh_one()
    assert "Authentication failed" in out
    assert "ignored fallback" not in out


def test_safe_error_message_uses_default_hint_for_unknown_exception():
    @logged_tool
    def unknown() -> str:
        try:
            raise ValueError("something internal")
        except Exception as e:
            return safe_error_message(e, hint="Custom default hint")

    out = unknown()
    assert "Custom default hint" in out
    assert "something internal" not in out
