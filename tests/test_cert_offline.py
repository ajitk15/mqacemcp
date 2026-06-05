"""Offline coverage for the certificate tool (`get_cert_details`).

No real HTTP — `get_cert_details` is a pure OFFLINE lookup over
`resources/cert_dump.csv`. These tests exercise:
- Tool registration + the `Certificate:` routing prefix.
- Substring search across hostname / alias / CN columns.
- The JSON-envelope contract (success-with-results, success-empty, and the
  six expected columns per row).

The shared `resources/cert_dump.csv` ships rows like `lodmq01.example.com`
with alias `mq-ssl-2026` — those are load-bearing for the assertions below.
"""
from __future__ import annotations

import json

import mqacemcpserver  # noqa: F401 — importing registers the tools
from server import cert_helpers

CERT_FIELDS = ("hostname", "alias", "cnname", "validfrom", "validuntil", "expiry")


def _tool(name: str):
    """Return the registered callable for a tool name."""
    return mqacemcpserver.mcp._tool_manager._tools[name].fn


# ---------------------------------------------------------------------------
# Registration + routing convention
# ---------------------------------------------------------------------------
def test_get_cert_details_is_registered():
    assert "get_cert_details" in mqacemcpserver.mcp._tool_manager._tools


def test_get_cert_details_docstring_opens_with_routing_prefix():
    doc = _tool("get_cert_details").__doc__ or ""
    assert doc.lstrip().startswith("Certificate:"), (
        "get_cert_details docstring must open with 'Certificate:' for LLM routing"
    )


# ---------------------------------------------------------------------------
# cert_helpers.search_certs — the offline search primitive
# ---------------------------------------------------------------------------
def test_search_certs_by_hostname_returns_all_fields():
    results = cert_helpers.search_certs("lodmq01")
    assert results, "expected at least one match for 'lodmq01'"
    for field in CERT_FIELDS:
        assert field in results[0], f"missing {field} in {results[0]}"


def test_search_certs_searches_all_columns_via_alias():
    """A substring that only appears in the alias column must still match."""
    results = cert_helpers.search_certs("mq-ssl-2026")
    assert any(r["alias"] == "mq-ssl-2026" for r in results), results


def test_search_certs_no_match_returns_empty_list():
    assert cert_helpers.search_certs("no-such-cert-anywhere") == []


# ---------------------------------------------------------------------------
# get_cert_details — JSON envelope contract
# ---------------------------------------------------------------------------
def test_get_cert_details_match_envelope():
    out = json.loads(_tool("get_cert_details")(search_string="lodmq01"))
    assert out["status"] == "success"
    assert out["results"], out
    assert set(CERT_FIELDS) <= set(out["results"][0].keys())


def test_get_cert_details_no_match_envelope():
    out = json.loads(_tool("get_cert_details")(search_string="no-such-cert-anywhere"))
    assert out["status"] == "success"
    assert out["results"] == []
