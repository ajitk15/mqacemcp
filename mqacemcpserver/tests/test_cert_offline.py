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

CERT_FIELDS = (
    "hostname",
    "alias",
    "cn_name",
    "valid_from",
    "valid_until",
    "expirydays",
)


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


def test_get_cert_details_exposes_expirydays():
    """expirydays must round-trip as an integer-parseable string per match."""
    out = json.loads(_tool("get_cert_details")(search_string="lodmq01"))
    row = out["results"][0]
    assert "expirydays" in row
    int(row["expirydays"])  # raises if not an integer string


def test_compute_expiry_days_is_live():
    """expirydays is computed against `today`, not read from the CSV column."""
    from datetime import date

    # 100 days before the cert's validuntil → expirydays should be 100.
    assert cert_helpers.compute_expiry_days(
        "Tue Jan 12 09:38:43 EST 2027", today=date(2026, 10, 4)
    ) == 100
    # A date in the past yields a negative count.
    assert cert_helpers.compute_expiry_days(
        "Sat Feb 15 08:00:00 EST 2025", today=date(2026, 6, 8)
    ) == -478
    # Unparseable input is tolerated.
    assert cert_helpers.compute_expiry_days("not a date") is None


def test_get_cert_details_includes_ace_nodes_for_ace_host():
    """An ACE cert host pivots to its node; a pure-MQ host has none."""
    ace = json.loads(_tool("get_cert_details")(search_string="lodace01"))["results"][0]
    assert ace["ace_nodes"] == ["NODE01"], ace
    mq = json.loads(_tool("get_cert_details")(search_string="lodmq01"))["results"][0]
    assert mq["ace_nodes"] == [], mq
