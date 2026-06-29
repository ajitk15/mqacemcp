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

import mqacemcpserver  # noqa: F401  — imports register the tools
from server.safety import MODIFY_BLOCKED_MSG


def _tool(name: str):
    """Return the registered callable for a tool name."""
    return mqacemcpserver.mcp._tool_manager._tools[name].fn


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
    actual = set(mqacemcpserver.mcp._tool_manager._tools.keys())
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
    result = asyncio.run(fn(queue_names=["DOES.NOT.EXIST.IN.MANIFEST"]))
    assert "not found in the manifest" in result


def test_mq_queue_inspect_restricted_only():
    """The shipped manifest's hosts (lopalhost) are NOT in the default
    lod/loq/lot allow-list, so a known queue must come back as restricted."""
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_names=["QL.IN.APP1"]))
    assert "restricted" in result.lower(), result


def test_mq_queue_inspect_fast_path_rejects_disallowed_host():
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(
        fn(queue_names=["QL.X"], qmgr_name="ANY", hostname="evil-host")
    )
    assert "not in the allowed list" in result, result


def test_mq_queue_inspect_local_queue_displays_all_attributes(monkeypatch):
    """A local-queue inspect must fetch the FULL attribute set (DISPLAY QLOCAL
    ... ALL) so property questions (persistence, MAXMSGL, CRDATE, …) can be
    answered — not just the old fixed CURDEPTH/MAXDEPTH/... subset."""
    from server import composite_tools

    captured: list[str] = []

    async def fake_run_mqsc_raw(qmgr, mqsc, hostname):
        captured.append(mqsc)
        # The chain resolver first probes the real type; report QLOCAL so it
        # proceeds to the full-attribute display below.
        if "DISPLAY QUEUE(QL.TEST) TYPE" in mqsc.upper():
            return "QUEUE(QL.TEST) TYPE(QLOCAL)"
        return f"[stub] {mqsc}"

    monkeypatch.setattr(composite_tools, "run_mqsc_raw", fake_run_mqsc_raw)
    fn = _tool("mq_queue_inspect")
    # FAST PATH on an allow-listed host so we reach the MQSC call.
    asyncio.run(fn(queue_names=["QL.TEST"], qmgr_name="QMTEST", hostname="loq-mq01"))

    assert any("DISPLAY QLOCAL(QL.TEST) ALL" in m for m in captured), captured


# ---------------------------------------------------------------------------
# mq_queue_inspect — alias -> remote -> local chain resolution
# ---------------------------------------------------------------------------
def _chain_stub(captured: list[tuple[str, str]]):
    """A run_mqsc_raw stub that emulates the QA.IN.APP2 alias->remote chain.

    QA.IN.APP2 on MQQMGR2 is a QALIAS -> TARGET(QR.IN.APP2); QR.IN.APP2 on
    MQQMGR2 is a QREMOTE -> RNAME(QA.IN.APP2) RQMNAME(MQQMGR1); QA.IN.APP2 on
    MQQMGR1 is the terminal QLOCAL.
    """

    async def fake(qmgr, mqsc, hostname):
        captured.append((qmgr.upper(), mqsc.upper()))
        u = mqsc.upper()
        qm = qmgr.upper()
        if "DISPLAY QUEUE(QA.IN.APP2) TYPE" in u:
            return (
                "QUEUE(QA.IN.APP2) TYPE(QALIAS)"
                if qm == "MQQMGR2"
                else "QUEUE(QA.IN.APP2) TYPE(QLOCAL)"
            )
        if "DISPLAY QALIAS(QA.IN.APP2)" in u:
            return "QUEUE(QA.IN.APP2) TYPE(QALIAS) TARGET(QR.IN.APP2) TARGTYPE(QUEUE)"
        if "DISPLAY QUEUE(QR.IN.APP2) TYPE" in u:
            return "QUEUE(QR.IN.APP2) TYPE(QREMOTE)"
        if "DISPLAY QREMOTE(QR.IN.APP2)" in u:
            return (
                "QUEUE(QR.IN.APP2) TYPE(QREMOTE) RNAME(QA.IN.APP2) "
                "RQMNAME(MQQMGR1) XMITQ(XMIT.Q.QM2)"
            )
        if "DISPLAY QLOCAL(QA.IN.APP2) ALL" in u:
            return "QUEUE(QA.IN.APP2) TYPE(QLOCAL) CURDEPTH(0) MAXDEPTH(5000)"
        return f"[stub] {mqsc}"

    return fake


def test_mq_queue_inspect_alias_to_remote_chain(monkeypatch):
    """An alias whose TARGET is a QREMOTE must resolve through the remote queue
    onto its destination QM — NOT be reported as 'QLOCAL not found'."""
    from server import composite_tools

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(composite_tools, "run_mqsc_raw", _chain_stub(captured))
    # MQQMGR1 lives on an allow-listed host so the chain is chased onto it.
    monkeypatch.setattr(
        composite_tools,
        "_resolve_target_host",
        lambda qmgr, host: ("loq-mq01", None),
    )

    fn = _tool("mq_queue_inspect")
    result = asyncio.run(
        fn(queue_names=["QA.IN.APP2"], qmgr_name="MQQMGR2", hostname="loq-mq01")
    )

    assert (
        "QA.IN.APP2(MQQMGR2) --> QR.IN.APP2(MQQMGR2) --> QA.IN.APP2(MQQMGR1)"
        in result
    ), result
    # The old bug: querying the remote queue as a local queue.
    assert not any(
        "DISPLAY QLOCAL(QR.IN.APP2)" in m for _, m in captured
    ), captured
    # The destination QLOCAL on MQQMGR1 must be inspected.
    assert ("MQQMGR1", "DISPLAY QLOCAL(QA.IN.APP2) ALL") in captured, captured


def test_mq_queue_inspect_remote_dest_not_in_manifest_stops(monkeypatch):
    """When the QREMOTE's RQMNAME is not in the manifest, the chain names the
    destination and stops — without any HTTP to the unknown QM."""
    from server import composite_tools

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(composite_tools, "run_mqsc_raw", _chain_stub(captured))
    # Honour an explicit host (the fast-path starting QM) but treat the
    # QREMOTE's RQMNAME (looked up with host=None) as unknown.
    monkeypatch.setattr(
        composite_tools,
        "_resolve_target_host",
        lambda qmgr, host: (host, None) if host else (None, "not in manifest"),
    )

    fn = _tool("mq_queue_inspect")
    result = asyncio.run(
        fn(queue_names=["QA.IN.APP2"], qmgr_name="MQQMGR2", hostname="loq-mq01")
    )

    assert "QA.IN.APP2(MQQMGR1)" in result, result
    assert "not in the manifest" in result, result
    # No call was made against the unknown destination QM.
    assert not any(qm == "MQQMGR1" for qm, _ in captured), captured


# ---------------------------------------------------------------------------
# mq_channel_inspect — discovery + allow-list branches
# ---------------------------------------------------------------------------
def test_mq_channel_inspect_not_in_manifest():
    fn = _tool("mq_channel_inspect")
    result = asyncio.run(fn(channel_names=["CH.DOES.NOT.EXIST"]))
    assert "not found in the manifest" in result


def test_mq_channel_inspect_fast_path_rejects_disallowed_host():
    fn = _tool("mq_channel_inspect")
    result = asyncio.run(
        fn(channel_names=["CH.X"], qmgr_name="ANY", hostname="prod-host")
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
            qmgr_names=["QMTEST"],
            hostnames=["loq-mq01"],
            mqsc_command="DEFINE QLOCAL(X)",
        )
    )
    assert "Modification requests are not permitted" in result, result
    # The leading MODIFY_BLOCKED_MSG title line should appear verbatim.
    title = MODIFY_BLOCKED_MSG.splitlines()[0]
    assert title in result


def test_mq_host_overview_rejects_disallowed_host():
    fn = _tool("mq_host_overview")
    result = asyncio.run(fn(hostnames=["evil-host"]))
    assert "not in the allowed list" in result, result


def test_mq_host_overview_warns_when_mqsc_without_qmgr():
    fn = _tool("mq_host_overview")
    result = asyncio.run(
        fn(hostnames=["loq-mq01"], mqsc_command="DISPLAY QMGR ALL")
    )
    assert "without `qmgr_name`" in result, result


# ---------------------------------------------------------------------------
# ace_search — scope handling + offline manifest reads
# ---------------------------------------------------------------------------
def test_ace_search_rejects_unknown_scope():
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=["x"], scope="bogus"))
    assert out["status"] == "error"
    assert "Unknown scope" in out["message"]


def test_ace_search_nodes_scope_lists_configured_nodes():
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=[""], scope="nodes"))
    assert out["status"] == "success"
    assert "nodes" in out
    # The shipped node_config.csv has NODE1..NODE4 — at least one must come back.
    assert isinstance(out["nodes"], list)


def test_ace_search_dump_scope_filters_by_substring():
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=["BIP"], scope="dump"))
    assert out["status"] == "success"
    assert "dump_matches" in out
    assert isinstance(out["dump_matches"], list)
    # Every match must mention BIP in some field for the substring search to
    # be honest.
    for row in out["dump_matches"]:
        haystack = " ".join(str(v) for v in row.values()).lower()
        assert "bip" in haystack


def test_ace_search_dump_pivots_cert_host_to_node():
    """A cert hostname (from get_cert_details) must resolve to its ACE node via
    the shared node_dump.csv — the hostname columns are aligned across the
    manifests so this cross-tool pivot works."""
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=["lodace01.example.com"], scope="dump"))
    assert out["status"] == "success"
    assert out["dump_matches"], out
    assert any(r["node"] == "NODE01" for r in out["dump_matches"]), out


def test_ace_search_default_scope_returns_both_sections():
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=["NODE"]))
    assert out["status"] == "success"
    assert out["scope"] == "all"
    assert "nodes" in out
    assert "dump_matches" in out


# ---------------------------------------------------------------------------
# ace_node_overview / ace_server_explore — happy-path error envelopes
# ---------------------------------------------------------------------------
def test_ace_node_overview_unknown_node():
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn(nodes=["NODE.DOES.NOT.EXIST"])))
    # When the node is missing from node_config.csv, fetch_ace returns an
    # error envelope per call. The composite preserves it without raising.
    assert out["node"] == "NODE.DOES.NOT.EXIST"
    assert out.get("status") != "success" or "message" in out


def test_ace_server_explore_unknown_node():
    fn = _tool("ace_server_explore")
    out = json.loads(asyncio.run(fn(node="NODE.DOES.NOT.EXIST", servers=["X"])))
    assert out["node"] == "NODE.DOES.NOT.EXIST"
    assert out["server"] == "X"


# ---------------------------------------------------------------------------
# get_cert_details — OFFLINE certificate inventory lookup
# ---------------------------------------------------------------------------
def test_get_cert_details_no_match_returns_empty_results():
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["no-such-cert-anywhere"]))
    assert out["status"] == "success"
    assert out["results"] == []


def test_get_cert_details_match_returns_expected_fields():
    """The shared cert_dump.csv ships hostnames like lodmq01.example.com."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["lodmq01"]))
    assert out["status"] == "success"
    assert out["results"], out
    row = out["results"][0]
    for field in (
        "hostname",
        "alias",
        "cn_name",
        "valid_from",
        "valid_until",
        "expirydays",
    ):
        assert field in row, f"missing {field} in {row}"


def test_get_cert_details_searches_all_fields():
    """A substring that only appears in the alias column must still match."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["mqweb-https"]))
    assert out["status"] == "success"
    assert any(r["alias"] == "mqweb-https" for r in out["results"]), out


def test_get_cert_details_exposes_expirydays():
    """expirydays must round-trip as an integer-parseable string per match."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["lodmq01"]))
    row = out["results"][0]
    assert "expirydays" in row
    int(row["expirydays"])  # raises if not an integer string


def test_get_cert_details_includes_ace_nodes():
    """A cert result surfaces the ACE node on its host (empty for an MQ host)."""
    fn = _tool("get_cert_details")
    ace = json.loads(fn(search_strings=["lodace01"]))["results"][0]
    assert ace["ace_nodes"] == ["NODE01"], ace
    mq = json.loads(fn(search_strings=["lodmq01"]))["results"][0]
    assert mq["ace_nodes"] == [], mq


# ---------------------------------------------------------------------------
# Multi-target (list[str]) support — one tool call, several objects
# ---------------------------------------------------------------------------
def test_mq_queue_inspect_multi_target_inspects_each():
    """Two queue names in one call → both are reported (banner + per-queue
    sections), and one missing queue does not suppress the other."""
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(
        fn(queue_names=["DOES.NOT.EXIST.A", "DOES.NOT.EXIST.B"])
    )
    assert "Inspecting 2 queues" in result, result
    assert "Queue: DOES.NOT.EXIST.A" in result, result
    assert "Queue: DOES.NOT.EXIST.B" in result, result
    assert result.count("not found in the manifest") == 2, result


def test_mq_queue_inspect_empty_list_is_handled():
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_names=[]))
    assert "No queue name supplied" in result, result


def test_mq_queue_inspect_single_element_has_no_banner():
    """A one-element list must behave exactly like the old single-queue call —
    no multi-target banner, identical not-found wording."""
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_names=["DOES.NOT.EXIST.IN.MANIFEST"]))
    assert "Inspecting" not in result, result
    assert "not found in the manifest" in result, result


def test_mq_channel_inspect_multi_target_inspects_each():
    fn = _tool("mq_channel_inspect")
    result = asyncio.run(
        fn(channel_names=["CH.NOPE.A", "CH.NOPE.B"])
    )
    assert "Inspecting 2 channels" in result, result
    assert "Channel: CH.NOPE.A" in result, result
    assert "Channel: CH.NOPE.B" in result, result
    assert result.count("not found in the manifest") == 2, result


def test_get_cert_details_multi_query_merges_and_tags():
    """Two search strings → merged results, each row tagged with the query that
    matched it; both hosts appear."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["lodmq01", "lodace01"]))
    assert out["status"] == "success"
    assert out["results"], out
    hosts = " ".join(r["hostname"] for r in out["results"])
    assert "lodmq01" in hosts and "lodace01" in hosts, out
    for row in out["results"]:
        assert "matched_query" in row, row
        assert isinstance(row["matched_query"], list) and row["matched_query"], row


def test_get_cert_details_empty_list_is_handled():
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=[]))
    assert out["status"] == "error"
    assert "No search string supplied" in out["message"], out


def test_mq_host_overview_multi_target_rejects_each_disallowed_host():
    """Two disallowed hosts in one call → banner + a rejection per host, with
    no outbound HTTP (allow-list fires first)."""
    fn = _tool("mq_host_overview")
    result = asyncio.run(fn(hostnames=["evil-a", "evil-b"]))
    assert "Inspecting 2 hosts/queue managers" in result, result
    assert "evil-a" in result and "evil-b" in result, result
    assert result.count("not in the allowed list") == 2, result


def test_mq_host_overview_no_args_still_single_default():
    """No targets → a single default-host overview (no multi banner)."""
    fn = _tool("mq_host_overview")
    result = asyncio.run(fn())
    assert "Inspecting" not in result, result
    assert "Host overview" in result, result


def test_ace_node_overview_multi_target_wraps_results():
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn(nodes=["GHOST.A", "GHOST.B"])))
    assert out["status"] == "success"
    assert out["count"] == 2, out
    assert len(out["nodes"]) == 2, out
    assert {n["node"] for n in out["nodes"]} == {"GHOST.A", "GHOST.B"}, out


def test_ace_node_overview_empty_list_is_handled():
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn(nodes=[])))
    assert out["status"] == "error"
    assert "No node supplied" in out["message"], out


def test_ace_server_explore_multi_target_wraps_results():
    fn = _tool("ace_server_explore")
    out = json.loads(
        asyncio.run(fn(node="NODE.DOES.NOT.EXIST", servers=["X", "Y"]))
    )
    assert out["status"] == "success"
    assert out["node"] == "NODE.DOES.NOT.EXIST"
    assert out["count"] == 2, out
    assert {s["server"] for s in out["servers"]} == {"X", "Y"}, out


def test_ace_search_multi_query_ors_and_dedups():
    """Multiple node queries OR together; duplicate queries collapse to one."""
    fn = _tool("ace_search")
    multi = json.loads(
        fn(search_strings=["NODE", "definitely-no-such-xyz"], scope="nodes")
    )
    assert multi["status"] == "success"
    assert multi["search_strings"] == ["NODE", "definitely-no-such-xyz"], multi
    # "NODE" matches at least one configured node; the no-match term adds none.
    assert len(multi["nodes"]) >= 1, multi
    only_node = json.loads(fn(search_strings=["NODE"], scope="nodes"))
    assert len(multi["nodes"]) == len(only_node["nodes"]), (multi, only_node)
    # Duplicate queries de-duplicate.
    dup = json.loads(fn(search_strings=["NODE", "NODE"], scope="nodes"))
    assert dup["search_strings"] == ["NODE"], dup
