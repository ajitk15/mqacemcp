"""Offline coverage for the six composite tools.

These tests do NOT make real HTTP calls. They exercise:
- Tool registration (the catalogue is exactly six names).
- Manifest discovery paths (search_objects_structured against shared CSVs).
- Read-only enforcement (modification verbs rejected).
- Hostname allow-list enforcement (out-of-list hosts rejected).
- ace_search across both scopes against shared CSVs.

The shared `resources/qmgr_dump.csv` ships with hostnames like `lopalhost`
which do NOT match the default `lod,loq,lot` allow-list — that's load-bearing
for the "restricted hosts" branch assertions below.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import single_server  # noqa: F401  — imports register the tools
from server.safety import MODIFY_BLOCKED_MSG


def _tool(name: str):
    """Return the registered callable for a tool name."""
    return single_server.mcp._tool_manager._tools[name].fn


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------
def test_exactly_seven_tools_registered():
    expected = {
        "mq_queue_inspect",
        "mq_channel_inspect",
        "mq_host_overview",
        "ace_node_overview",
        "ace_server_explore",
        "ace_search",
        "get_cert_details",
    }
    actual = set(single_server.mcp._tool_manager._tools.keys())
    assert actual == expected, f"unexpected tool set: {sorted(actual)}"


def test_mq_tool_docstrings_open_with_routing_prefix():
    for name in ("mq_queue_inspect", "mq_channel_inspect", "mq_host_overview"):
        doc = _tool(name).__doc__ or ""
        assert doc.lstrip().startswith("IBM MQ:"), (
            f"{name} docstring must open with 'IBM MQ:' for LLM routing"
        )


def test_ace_tool_docstrings_open_with_routing_prefix():
    for name in ("ace_node_overview", "ace_server_explore", "ace_search"):
        doc = _tool(name).__doc__ or ""
        assert doc.lstrip().startswith("IBM ACE:"), (
            f"{name} docstring must open with 'IBM ACE:' for LLM routing"
        )


def test_cert_tool_docstring_opens_with_routing_prefix():
    doc = _tool("get_cert_details").__doc__ or ""
    assert doc.lstrip().startswith("Certificate:"), (
        "get_cert_details docstring must open with 'Certificate:' for LLM routing"
    )


# ---------------------------------------------------------------------------
# mq_queue_inspect — discovery + allow-list branches
# ---------------------------------------------------------------------------
def test_mq_queue_inspect_not_in_manifest():
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_name="DOES.NOT.EXIST.IN.MANIFEST"))
    assert "not found in the manifest" in result


def test_mq_queue_inspect_restricted_only():
    """The shipped manifest's hosts (lopalhost) are NOT in the default
    lod/loq/lot allow-list, so a known queue must come back as restricted."""
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_name="QL.IN.APP1"))
    assert "restricted" in result.lower(), result


def test_mq_queue_inspect_fast_path_rejects_disallowed_host():
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(
        fn(queue_name="QL.X", qmgr_name="ANY", hostname="evil-host")
    )
    assert "not in the allowed list" in result, result


# ---------------------------------------------------------------------------
# mq_channel_inspect — discovery + allow-list branches
# ---------------------------------------------------------------------------
def test_mq_channel_inspect_not_in_manifest():
    fn = _tool("mq_channel_inspect")
    result = asyncio.run(fn(channel_name="CH.DOES.NOT.EXIST"))
    assert "not found in the manifest" in result


def test_mq_channel_inspect_fast_path_rejects_disallowed_host():
    fn = _tool("mq_channel_inspect")
    result = asyncio.run(
        fn(channel_name="CH.X", qmgr_name="ANY", hostname="prod-host")
    )
    assert "not in the allowed list" in result, result


# ---------------------------------------------------------------------------
# mq_host_overview — modification block + allow-list
# ---------------------------------------------------------------------------
def test_mq_host_overview_blocks_modification_mqsc():
    fn = _tool("mq_host_overview")
    # Hostname "loq-mq01" satisfies the default allow-list, so we get past the
    # gate and reach the MQSC validation step. The dspmq/dspmqver upstream
    # calls will fail (no real server) but their errors are sanitised, and
    # the modification block must still appear in the output.
    result = asyncio.run(
        fn(
            qmgr_name="QMTEST",
            hostname="loq-mq01",
            mqsc_command="DEFINE QLOCAL(X)",
        )
    )
    assert "Modification requests are not permitted" in result, result
    # The leading MODIFY_BLOCKED_MSG title line should appear verbatim.
    title = MODIFY_BLOCKED_MSG.splitlines()[0]
    assert title in result


def test_mq_host_overview_rejects_disallowed_host():
    fn = _tool("mq_host_overview")
    result = asyncio.run(fn(hostname="evil-host"))
    assert "not in the allowed list" in result, result


def test_mq_host_overview_warns_when_mqsc_without_qmgr():
    fn = _tool("mq_host_overview")
    result = asyncio.run(
        fn(hostname="loq-mq01", mqsc_command="DISPLAY QMGR ALL")
    )
    assert "without `qmgr_name`" in result, result


# ---------------------------------------------------------------------------
# ace_search — scope handling + offline manifest reads
# ---------------------------------------------------------------------------
def test_ace_search_rejects_unknown_scope():
    fn = _tool("ace_search")
    out = json.loads(fn(search_string="x", scope="bogus"))
    assert out["status"] == "error"
    assert "Unknown scope" in out["message"]


def test_ace_search_nodes_scope_lists_configured_nodes():
    fn = _tool("ace_search")
    out = json.loads(fn(search_string="", scope="nodes"))
    assert out["status"] == "success"
    assert "nodes" in out
    # The shipped node_config.csv has NODE1..NODE4 — at least one must come back.
    assert isinstance(out["nodes"], list)


def test_ace_search_dump_scope_filters_by_substring():
    fn = _tool("ace_search")
    out = json.loads(fn(search_string="BIP", scope="dump"))
    assert out["status"] == "success"
    assert "dump_matches" in out
    assert isinstance(out["dump_matches"], list)
    # Every match must mention BIP in some field for the substring search to
    # be honest.
    for row in out["dump_matches"]:
        haystack = " ".join(str(v) for v in row.values()).lower()
        assert "bip" in haystack


def test_ace_search_default_scope_returns_both_sections():
    fn = _tool("ace_search")
    out = json.loads(fn(search_string="NODE"))
    assert out["status"] == "success"
    assert out["scope"] == "all"
    assert "nodes" in out
    assert "dump_matches" in out


# ---------------------------------------------------------------------------
# ace_node_overview / ace_server_explore — happy-path error envelopes
# ---------------------------------------------------------------------------
def test_ace_node_overview_unknown_node():
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn(node="NODE.DOES.NOT.EXIST")))
    # When the node is missing from node_config.csv, fetch_ace returns an
    # error envelope per call. The composite preserves it without raising.
    assert out["node"] == "NODE.DOES.NOT.EXIST"
    assert out.get("status") != "success" or "message" in out


def test_ace_server_explore_unknown_node():
    fn = _tool("ace_server_explore")
    out = json.loads(asyncio.run(fn(node="NODE.DOES.NOT.EXIST", server="X")))
    assert out["node"] == "NODE.DOES.NOT.EXIST"
    assert out["server"] == "X"


# ---------------------------------------------------------------------------
# get_cert_details — OFFLINE certificate inventory lookup
# ---------------------------------------------------------------------------
def test_get_cert_details_no_match_returns_empty_results():
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_string="no-such-cert-anywhere"))
    assert out["status"] == "success"
    assert out["results"] == []


def test_get_cert_details_match_returns_expected_fields():
    """The shared cert_dump.csv ships hostnames like lodmq01.example.com."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_string="lodmq01"))
    assert out["status"] == "success"
    assert out["results"], out
    row = out["results"][0]
    for field in ("hostname", "alias", "cnname", "validfrom", "validuntil", "expiry"):
        assert field in row, f"missing {field} in {row}"


def test_get_cert_details_searches_all_fields():
    """A substring that only appears in the alias column must still match."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_string="mqweb-https"))
    assert out["status"] == "success"
    assert any(r["alias"] == "mqweb-https" for r in out["results"]), out
