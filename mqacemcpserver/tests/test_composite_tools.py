"""Offline coverage for the composite tools.

These tests do NOT make real HTTP calls. They exercise:
- Tool registration (the catalogue is exactly ten names).
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
def test_exactly_ten_tools_registered():
    expected = {
        "mq_queue_inspect",
        "mq_channel_inspect",
        "mq_host_overview",
        "mq_connection_verify",
        "ace_node_overview",
        "ace_server_explore",
        "ace_resource_inspect",
        "ace_search",
        "ace_connection_verify",
        "get_cert_details",
    }
    actual = set(mqacemcpserver.mcp._tool_manager._tools.keys())
    assert actual == expected, f"unexpected tool set: {sorted(actual)}"


def test_mq_tool_docstrings_open_with_routing_prefix():
    for name in (
        "mq_queue_inspect",
        "mq_channel_inspect",
        "mq_host_overview",
        "mq_connection_verify",
    ):
        doc = _tool(name).__doc__ or ""
        assert doc.lstrip().startswith("IBM MQ:"), (
            f"{name} docstring must open with 'IBM MQ:' for LLM routing"
        )


def test_ace_tool_docstrings_open_with_routing_prefix():
    for name in (
        "ace_node_overview",
        "ace_server_explore",
        "ace_resource_inspect",
        "ace_search",
        "ace_connection_verify",
    ):
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
    """The shipped manifest's hosts (localhost) are NOT in the default
    lod/loq/lot allow-list, so a known queue must come back as restricted."""
    fn = _tool("mq_queue_inspect")
    result = asyncio.run(fn(queue_names=["QL.INPUT"]))
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
    manifests so this cross-tool pivot works. The seed data runs both nodes on
    `localhost`."""
    fn = _tool("ace_search")
    out = json.loads(fn(search_strings=["localhost"], scope="dump"))
    assert out["status"] == "success"
    assert out["dump_matches"], out
    assert any(r["node"] == "NODE1" for r in out["dump_matches"]), out


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
    """The shared cert_dump.csv is searchable by alias / CN / hostname."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["mq-ssl-2026"]))
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
    out = json.loads(fn(search_strings=["mq-ssl-2026"]))
    row = out["results"][0]
    assert "expirydays" in row
    int(row["expirydays"])  # raises if not an integer string


def test_get_cert_details_includes_ace_nodes():
    """A cert result surfaces the ACE node(s) on its host. The seed data runs
    both integration nodes on `localhost`, so every cert pivots to them."""
    fn = _tool("get_cert_details")
    row = json.loads(fn(search_strings=["mq-ssl-2026"]))["results"][0]
    assert row["ace_nodes"] == ["NODE1", "NODE2"], row


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
    matched it; both queries are represented."""
    fn = _tool("get_cert_details")
    out = json.loads(fn(search_strings=["mq-ssl-2026", "ace-admin-tls"]))
    assert out["status"] == "success"
    assert out["results"], out
    aliases = " ".join(r["alias"] for r in out["results"])
    assert "mq-ssl-2026" in aliases and "ace-admin-tls" in aliases, out
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


def test_ace_node_overview_empty_list_discovers_every_node():
    """An empty list means "the whole estate", not an error.

    The hosting client gets one tool call per question, so it cannot be asked
    which node to look at — an omitted target is resolved from node_config.csv.
    """
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn(nodes=[])))
    assert out["status"] == "success", out
    assert out["discovered_targets"] == ["NODE1", "NODE2"], out


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


# ---------------------------------------------------------------------------
# mq_connection_verify — OFFLINE fact-check of pasted connection details
# ---------------------------------------------------------------------------
# Fixture facts (resources/qmgr_dump.csv): MQREPO1 has listener PORT(1414) and
# channel MQREPO1.CLUSRCVR with CONNAME('server1(1414)'); MQQM1 uses PORT(1415).
def test_mq_connection_verify_all_fields_correct():
    fn = _tool("mq_connection_verify")
    result = asyncio.run(
        fn(
            qmgr_name="MQREPO1",
            hostname="server1",
            port=1414,
            channel="MQREPO1.CLUSRCVR",
        )
    )
    assert "all supplied details check out" in result, result
    assert "❌" not in result, result


def test_mq_connection_verify_conname_nested_parens_parse():
    """Guards the _parse_attr pitfall: CONNAME('server1(1414)') must yield host
    server1 and port 1414 (not 'server1(1414' truncated at the first paren)."""
    fn = _tool("mq_connection_verify")
    result = asyncio.run(
        fn(qmgr_name="MQREPO1", channel="MQREPO1.CLUSRCVR", hostname="server1", port=1414)
    )
    assert "server1(1414)" in result, result
    assert "✅ Host: 'server1' matches" in result, result
    assert "✅ Port: 1414 matches" in result, result


def test_mq_connection_verify_wrong_port_mismatch():
    fn = _tool("mq_connection_verify")
    result = asyncio.run(fn(qmgr_name="MQREPO1", port=9999))
    assert "❌ Port" in result, result
    assert "1414" in result, result  # names the authoritative port


def test_mq_connection_verify_unknown_channel_not_found():
    fn = _tool("mq_connection_verify")
    result = asyncio.run(fn(qmgr_name="MQREPO1", channel="CH.NOPE.NOWHERE"))
    assert "❌ Channel" in result, result
    assert "NOT defined on MQREPO1" in result, result
    # Lists the QM's real channels so the user can correct the claim.
    assert "MQREPO1.CLUSRCVR" in result, result


def test_mq_connection_verify_host_mismatch_against_conname():
    fn = _tool("mq_connection_verify")
    result = asyncio.run(
        fn(qmgr_name="MQREPO1", hostname="wrong-host", channel="MQREPO1.CLUSRCVR")
    )
    assert "❌ Host" in result, result
    assert "server1" in result, result  # the authoritative CONNAME host


def test_mq_connection_verify_unknown_qmgr_stops_early():
    fn = _tool("mq_connection_verify")
    result = asyncio.run(fn(qmgr_name="NOSUCHQM", port=1414))
    assert "NOT in the manifest" in result, result
    # A wrong QM short-circuits — no per-field port line is emitted.
    assert "✅ Port" not in result and "❌ Port" not in result, result


# ---------------------------------------------------------------------------
# ace_connection_verify — OFFLINE fact-check against node_config.csv
# ---------------------------------------------------------------------------
# Fixture facts (resources/node_config.csv): NODE1|localhost|4414.
def test_ace_connection_verify_all_fields_correct():
    fn = _tool("ace_connection_verify")
    result = fn(node="NODE1", host="localhost", port=4414)
    assert "all supplied details check out" in result, result
    assert "❌" not in result, result


def test_ace_connection_verify_wrong_port_mismatch():
    fn = _tool("ace_connection_verify")
    result = fn(node="NODE1", port=9999)
    assert "❌ Port" in result, result
    assert "4414" in result, result  # the configured Admin REST port


def test_ace_connection_verify_unknown_node_stops_early():
    fn = _tool("ace_connection_verify")
    result = fn(node="NODE.DOES.NOT.EXIST", host="localhost")
    assert "NOT in node_config.csv" in result, result
    assert "✅ Host" not in result and "❌ Host" not in result, result


# ---------------------------------------------------------------------------
# Manifest discovery — prefix inference must not hide mis-prefixed objects
# ---------------------------------------------------------------------------


def test_alias_named_with_qlocal_prefix_is_still_discoverable():
    """A QALIAS may be named QL.* — the prefix rule must not filter it out.

    `QL.ADMIN.REQUEST.ALIAS` is a QALIAS on MQREPO1, but its `QL.` prefix
    infers QLOCAL. Before the fallback, that filter emptied the result and the
    alias reported as "not found in the manifest".
    """
    from server.mq_helpers import search_objects_structured

    results = search_objects_structured("QL.ADMIN.REQUEST.ALIAS")
    assert results, "alias must be discoverable without an explicit qmgr_name"
    assert {r["object_type"].upper() for r in results} == {"QALIAS"}
    assert {r["qmgr"] for r in results} == {"MQREPO1"}


def test_prefix_inference_still_narrows_when_it_matches():
    """The widening is a fallback only — a real QL.* local queue stays narrowed."""
    from server.mq_helpers import search_objects_structured

    results = search_objects_structured("QL.INPUT")
    assert results
    assert {r["object_type"].upper() for r in results} == {"QLOCAL"}


def test_explicit_object_type_is_authoritative_and_stays_strict():
    """An object_type supplied by the caller must not be widened away."""
    from server.mq_helpers import search_objects_structured

    assert search_objects_structured("QL.ADMIN.REQUEST.ALIAS", "QLOCAL") == []
    assert search_objects_structured("QL.ADMIN.REQUEST.ALIAS", "QALIAS")


# ---------------------------------------------------------------------------
# Target discovery — the client makes ONE call, so an omitted target must be
# resolved from the manifests rather than asked back.
# ---------------------------------------------------------------------------


def test_discovery_helpers_read_the_manifests():
    from server.composite_tools import (
        _all_configured_nodes,
        _all_manifest_qmgrs,
        _nodes_hosting,
    )

    assert _all_configured_nodes() == ["NODE1", "NODE2"]
    # An EG name alone must resolve to every node that hosts it.
    assert _nodes_hosting(["ACE_DEMO_MESSAGING"]) == ["NODE1", "NODE2"]
    # So must an application name.
    assert _nodes_hosting(["ACE_Salesforce_Leads"]) == ["NODE1", "NODE2"]
    assert set(_all_manifest_qmgrs()) == {
        "MQREPO1",
        "MQREPO2",
        "MQQM1",
        "MQNODE1",
        "MQNODE2",
    }


def test_ace_node_overview_with_no_nodes_covers_every_configured_node():
    """Omitting `nodes` must overview the whole estate, not error."""
    fn = _tool("ace_node_overview")
    out = json.loads(asyncio.run(fn()))
    assert out.get("discovered_targets") == ["NODE1", "NODE2"]
    assert {n["node"] for n in out["nodes"]} == {"NODE1", "NODE2"}


def test_ace_server_explore_without_node_resolves_hosting_nodes():
    """An EG named with no node must fan out to every node hosting it."""
    fn = _tool("ace_server_explore")
    out = json.loads(asyncio.run(fn(servers=["ACE_DEMO_MESSAGING"])))
    assert out["discovered_nodes"] == ["NODE1", "NODE2"]
    assert out["count"] == 2
    assert {s["node"] for s in out["servers"]} == {"NODE1", "NODE2"}


def test_ace_server_explore_unknown_server_says_so_without_asking():
    fn = _tool("ace_server_explore")
    out = json.loads(asyncio.run(fn(servers=["NO.SUCH.EG"])))
    assert out["status"] == "error"
    assert "NO.SUCH.EG" in out["message"]


def test_ace_server_explore_with_explicit_node_is_unchanged():
    """The existing single-server-on-a-named-node shape must not move."""
    fn = _tool("ace_server_explore")
    out = json.loads(asyncio.run(fn(servers=["X"], node="GHOST")))
    assert out["node"] == "GHOST"
    assert out["server"] == "X"


def test_mq_host_overview_fans_mqsc_across_every_queue_manager():
    """An MQSC with no named QM must reach the whole estate, not be dropped."""
    fn = _tool("mq_host_overview")
    out = asyncio.run(fn(mqsc_command="DISPLAY QMGR DEADQ"))
    assert "every queue manager in the manifest was discovered" in out
    for qm in ("MQREPO1", "MQREPO2", "MQQM1", "MQNODE1", "MQNODE2"):
        assert qm in out


# ---------------------------------------------------------------------------
# ace_server_explore — message flows live under an APPLICATION, never under the
# integration server. `/servers/<s>/messageflows` does not exist in the ACE
# Admin REST API (the server's children are applications, restApis, services,
# sharedLibraries, policies, ...), so asking for it 404'd on every EG-level
# question and surfaced a spurious "Endpoint not found" note on an otherwise
# complete answer.
# ---------------------------------------------------------------------------
def _ace_apps_payload(names: list[str]) -> str:
    return json.dumps(
        {
            "status": "success",
            "raw_response": {
                "children": [
                    {"name": n, "active": {"isRunning": True, "state": "started"}}
                    for n in names
                ]
            },
        }
    )


def _ace_flows_payload(entries: list[tuple]) -> str:
    return json.dumps(
        {
            "status": "success",
            "raw_response": {
                "children": [
                    {"name": n, "active": {"isRunning": r, "state": s}}
                    for n, r, s in entries
                ]
            },
        }
    )


def _stub_fetch_ace(monkeypatch, handler):
    """Record every requested path; answer each from `handler(path)`."""
    from server import composite_tools

    paths: list[str] = []

    # Signature must mirror fetch_ace's own (`target_node`, not `node`) — the
    # call sites also pass node=... as a kwarg for the log record.
    async def fake_fetch_ace(target_node, path, component, **kwargs):
        paths.append(path)
        return handler(path)

    monkeypatch.setattr(composite_tools, "fetch_ace", fake_fetch_ace)
    return paths


def _apps_then_flows(app_names, flows_by_app=None, errors=None):
    flows_by_app = flows_by_app or {}
    errors = errors or {}

    def handler(path: str) -> str:
        if path.endswith("/applications?depth=2"):
            return _ace_apps_payload(app_names)
        for app in app_names:
            if f"/applications/{app}/messageflows" in path:
                if app in errors:
                    return json.dumps({"status": "error", "message": errors[app]})
                return _ace_flows_payload(
                    flows_by_app.get(app, [("flow_" + app, True, "started")])
                )
        return json.dumps({"status": "error", "message": "unexpected path"})

    return handler


def test_server_explore_never_requests_server_level_messageflows(monkeypatch):
    """Regression guard for the spurious 'Endpoint not found' note."""
    from server.composite_tools import _server_explore_one

    paths = _stub_fetch_ace(monkeypatch, _apps_then_flows(["AppA", "AppB"]))
    env = asyncio.run(_server_explore_one("NODE1", "EG1", None))

    assert "/servers/EG1/messageflows?depth=2" not in paths, paths
    assert not any(
        p.startswith("/servers/EG1/messageflows") for p in paths
    ), paths
    assert "message_flows_error" not in env
    assert "message_flows_errors" not in env


def test_server_explore_fetches_flows_once_per_application(monkeypatch):
    from server.composite_tools import _server_explore_one

    paths = _stub_fetch_ace(monkeypatch, _apps_then_flows(["AppA", "AppB"]))
    asyncio.run(_server_explore_one("NODE1", "EG1", None))

    assert paths == [
        "/servers/EG1/applications?depth=2",
        "/servers/EG1/applications/AppA/messageflows?depth=2",
        "/servers/EG1/applications/AppB/messageflows?depth=2",
    ]


def test_server_explore_nests_flows_with_run_state(monkeypatch):
    """The live path must report run state, like the offline dump path does."""
    from server.composite_tools import _server_explore_one

    handler = _apps_then_flows(
        ["AmazonS3", "HTTP_Multiple_Requests"],
        {
            "AmazonS3": [("CreateItem", False, "failed")],
            "HTTP_Multiple_Requests": [("main", True, "started")],
        },
    )
    _stub_fetch_ace(monkeypatch, handler)
    env = asyncio.run(_server_explore_one("NODE1", "EG1", None))

    by_name = {a["name"]: a for a in env["applications"]}
    assert by_name["AmazonS3"]["message_flows"] == [
        {"name": "CreateItem", "running": False, "state": "failed"}
    ]
    assert by_name["HTTP_Multiple_Requests"]["message_flows"] == [
        {"name": "main", "running": True, "state": "started"}
    ]


def test_server_explore_reports_a_genuine_per_app_flow_failure(monkeypatch):
    """Real failures must still surface — only the phantom endpoint is gone."""
    from server.composite_tools import _server_explore_one

    handler = _apps_then_flows(
        ["Good", "Bad"], errors={"Bad": "Node unreachable"}
    )
    _stub_fetch_ace(monkeypatch, handler)
    env = asyncio.run(_server_explore_one("NODE1", "EG1", None))

    assert env["message_flows_errors"] == {"Bad": "Node unreachable"}
    # A flow failure must not cost us the application listing.
    assert [a["name"] for a in env["applications"]] == ["Good", "Bad"]
    by_name = {a["name"]: a for a in env["applications"]}
    assert by_name["Good"]["message_flows"]
    assert "message_flows" not in by_name["Bad"]


def test_server_explore_explicit_application_keeps_top_level_flows(monkeypatch):
    """Naming an application still issues exactly two calls, flows at top level."""
    from server.composite_tools import _server_explore_one

    handler = _apps_then_flows(
        ["AmazonS3", "Other"],
        {"AmazonS3": [("CreateItem", False, "failed")]},
    )
    paths = _stub_fetch_ace(monkeypatch, handler)
    env = asyncio.run(_server_explore_one("NODE1", "EG1", "AmazonS3"))

    assert paths == [
        "/servers/EG1/applications?depth=2",
        "/servers/EG1/applications/AmazonS3/messageflows?depth=2",
    ]
    assert env["application"] == "AmazonS3"
    assert env["message_flows"] == [
        {"name": "CreateItem", "running": False, "state": "failed"}
    ]


# ---------------------------------------------------------------------------
# ace_resource_inspect — the resource-manager subtree (cache, JVM, connectors)
#
# The gap these lock down: "is cache enabled for EG X" used to be
# unanswerable, because no tool reached
# /apiv2/servers/<eg>/resource-managers. `cacheOn` lives ONLY there.
# ---------------------------------------------------------------------------
_RM_AVAILABLE = [
    "activity-log-manager",
    "database-connection-manager",
    "esql-manager",
    "global-cache",
    "http-connector",
    "https-connector",
    "jvm-manager",
    "kafka-manager",
    "mq-connection-manager",
    "nodejs",
    "odm",
    "opentelemetry-manager",
    "redis-connection-manager",
    "xpath-cache",
]


def _resolve(requested):
    from server.composite_tools import _resolve_rm_names

    return _resolve_rm_names(requested, _RM_AVAILABLE)


def test_resolve_rm_cache_returns_both_caches():
    """"cache" is ambiguous on an integration server — answer both, not one."""
    resolved, unknown, hints = _resolve(["cache"])
    assert resolved == ["global-cache", "xpath-cache"], resolved
    assert unknown == []
    assert hints == {}


def test_resolve_rm_accepts_spacing_and_underscore_spellings():
    for term in ("Global Cache", "global_cache", "GLOBAL-CACHE"):
        resolved, unknown, _hints = _resolve([term])
        assert resolved == ["global-cache"], (term, resolved)
        assert unknown == [], (term, unknown)


def test_resolve_rm_aliases_and_suffix_completion():
    resolved, _unknown, _hints = _resolve(["jvm", "kafka", "mq", "https"])
    assert resolved == [
        "jvm-manager",
        "kafka-manager",
        "mq-connection-manager",
        "https-connector",
    ], resolved


def test_resolve_rm_dedups_overlapping_terms():
    resolved, _unknown, _hints = _resolve(["cache", "global-cache", "xpath"])
    assert resolved == ["global-cache", "xpath-cache"], resolved


def test_resolve_rm_single_close_match_auto_resolves():
    """One unambiguous near-match is a typo, not a question."""
    resolved, unknown, hints = _resolve(["kafk4"])
    assert resolved == ["kafka-manager"], resolved
    assert unknown == []
    assert hints == {}


def test_resolve_rm_unknown_with_suggestions_populates_did_you_mean():
    """The USEFUL half of the report must not regress into silence."""
    resolved, unknown, hints = _resolve(["conector"])
    assert resolved == []
    assert unknown == ["conector"]
    assert {"http-connector", "https-connector"} <= set(hints["conector"]), hints


def test_resolve_rm_unknown_without_suggestions_omits_did_you_mean():
    """No close match ⇒ absent from `did_you_mean`, never mapped to `[]`.

    `{"zzzz": []}` reads as "here are the suggestions: none", which is
    indistinguishable from a truncation bug to a reader and to the LLM.
    """
    resolved, unknown, hints = _resolve(["totally-bogus-thing"])
    assert resolved == []
    assert unknown == ["totally-bogus-thing"]
    assert hints == {}, hints


def test_resolve_rm_mixes_known_unknown_and_suggestible():
    resolved, unknown, hints = _resolve(["kafka", "conector", "zzzz"])
    assert resolved == ["kafka-manager"], resolved
    assert unknown == ["conector", "zzzz"], unknown
    assert list(hints) == ["conector"], hints


def _rm_payload(entries):
    """A fetch_ace success envelope shaped like /resource-managers?depth=2."""
    children = []
    for name, props, active in entries:
        children.append(
            {
                "name": name,
                "type": "resourceManager",
                "properties": props,
                "active": active,
                "descriptiveProperties": {
                    "className": "ComIbmCacheManager",
                    "isDynamic": "false",
                },
            }
        )
    return json.dumps(
        {"status": "success", "raw_response": {"children": children}}
    )


_CACHE_ENTRIES = [
    ("global-cache", {"identifier": "GlobalCache", "cacheOn": False}, {"cacheOn": False, "status": True}),
    ("xpath-cache", {"identifier": "ComIbmXPathCache", "mode": True}, {"mode": True}),
    ("jvm-manager", {"identifier": "JVMManager"}, {}),
    ("kafka-manager", {"identifier": "KafkaManager"}, {}),
]


def test_resource_inspect_uses_the_hyphenated_uri(monkeypatch):
    """`/resourceManagers` (the children KEY) 404s — the URI form is required."""
    from server.composite_tools import _resource_inspect_one

    paths = _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    asyncio.run(_resource_inspect_one("NODE1", "EG1", ["cache"]))

    assert paths == ["/servers/EG1/resource-managers?depth=2"], paths


def test_resource_inspect_reports_cache_on(monkeypatch):
    from server.composite_tools import _resource_inspect_one

    _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    env = asyncio.run(_resource_inspect_one("NODE1", "EG1", ["cache"]))

    names = [r["name"] for r in env["resource_managers"]]
    assert names == ["global-cache", "xpath-cache"], names
    gc = env["resource_managers"][0]
    assert gc["configured"]["cacheOn"] is False
    assert gc["active"]["cacheOn"] is False
    assert gc["identifier"] == "GlobalCache"
    assert env["selected_by"] == "requested"
    # Every name ships regardless, so a follow-up needs no discovery call.
    assert "kafka-manager" in env["available_resource_managers"]


def test_resource_inspect_default_selection_is_curated(monkeypatch):
    """No named manager ⇒ a short curated set, never the full ~30KB payload."""
    from server.composite_tools import _resource_inspect_one

    _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    env = asyncio.run(_resource_inspect_one("NODE1", "EG1", []))

    names = [r["name"] for r in env["resource_managers"]]
    assert env["selected_by"] == "default"
    assert names == ["global-cache", "jvm-manager", "kafka-manager"], names
    assert "xpath-cache" not in names
    assert len(env["available_resource_managers"]) == 4


def test_resource_inspect_surfaces_upstream_error(monkeypatch):
    from server.composite_tools import _resource_inspect_one

    _stub_fetch_ace(
        monkeypatch,
        lambda p: json.dumps({"status": "error", "message": "⚠️ nope (ref abc)"}),
    )
    env = asyncio.run(_resource_inspect_one("NODE1", "EG1", ["cache"]))

    assert env["resource_managers_error"] == "⚠️ nope (ref abc)"
    assert "resource_managers" not in env


def test_ace_resource_inspect_empty_servers_triggers_discovery(monkeypatch):
    """`servers=[]` sweeps the estate; it used to be a hard error.

    This is the exact call the orchestrator made for "list all global cache
    enabled EGs from NODE1" (`_as_str_list` strips the blank in `[""]` to
    `[]`), so it stays pinned.
    """
    _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": ["EG_A"], "NODE2": ["EG_B"]}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(servers=[])))

    assert out["status"] == "success", out
    assert out["discovered_targets"] == ["NODE1", "NODE2"], out
    assert {s["server"] for s in out["servers"]} == {"EG_A", "EG_B"}, out


def test_ace_resource_inspect_unknown_node():
    """Unknown node + unresolvable server ⇒ a named error, not a blind 404."""
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(node="NODE.DOES.NOT.EXIST", servers=["X"]))
    )
    assert out["status"] == "error", out
    assert out["servers"] == ["X"]
    assert out["unknown_servers"] == ["X"], out
    assert "NODE.DOES.NOT.EXIST" in out["message"]
    # The node could not be listed either; that must stay visible.
    assert out["node_errors"][0]["node"] == "NODE.DOES.NOT.EXIST", out


def test_ace_resource_inspect_multi_target_wraps_results(monkeypatch):
    """Two resolvable servers on one node wrap into the list envelope."""
    _stub_fetch_ace(
        monkeypatch,
        _sweep_handler({"NODE1": ["ACE_DEMO_CACHE", "ACE_DEMO_CONNECTORS"]}),
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(
            fn(node="NODE1", servers=["ACE_DEMO_CACHE", "ACE_DEMO_CONNECTORS"])
        )
    )
    assert out["status"] == "success"
    assert out["node"] == "NODE1"
    assert out["count"] == 2, out
    assert {s["server"] for s in out["servers"]} == {
        "ACE_DEMO_CACHE",
        "ACE_DEMO_CONNECTORS",
    }, out


def test_ace_resource_inspect_discovers_hosting_nodes(monkeypatch):
    """An EG named with no node resolves to every node hosting it."""
    from server.composite_tools import _resource_inspect_one  # noqa: F401

    _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=["ACE_DEMO_CONNECTORS"], resource_managers=["cache"]))
    )

    assert out["discovered_nodes"] == ["NODE1", "NODE2"], out
    assert out["requested_resource_managers"] == ["cache"]
    assert out["count"] == 2


# ---------------------------------------------------------------------------
# ace_resource_inspect sweeps — `servers` omitted
#
# The gap these lock down: "list all global cache enabled EGs from NODE1" used
# to fail with "No server supplied", because `servers` was required while
# `node` was optional — the opposite of what a node-scoped question needs.
# ---------------------------------------------------------------------------
def _sweep_handler(servers_by_node, node_errors=()):
    """Serve both /servers?depth=1 (discovery) and /resource-managers?depth=2.

    The stub's fetch_ace signature drops `target_node`, so the node is
    recovered from the ordering of the discovery calls instead: each
    /servers?depth=1 call is answered from `order` in turn.
    """
    order = list(servers_by_node)
    seen = {"i": 0}

    def handler(path: str) -> str:
        if path == "/servers?depth=1":
            node = order[seen["i"]]
            seen["i"] += 1
            if node in node_errors:
                return json.dumps({"status": "error", "message": f"{node} unreachable"})
            return json.dumps(
                {
                    "status": "success",
                    "raw_response": {
                        "children": [{"name": n} for n in servers_by_node[node]]
                    },
                }
            )
        return _rm_payload(_CACHE_ENTRIES)

    return handler


def test_resource_inspect_sweeps_live_servers_on_a_node(monkeypatch):
    paths = _stub_fetch_ace(
        monkeypatch, _sweep_handler({"NODE1": ["EG_A", "EG_B", "EG_C"]})
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(node="NODE1", resource_managers=["cache"])))

    assert paths[0] == "/servers?depth=1", paths
    assert paths[1:] == [
        "/servers/EG_A/resource-managers?depth=2",
        "/servers/EG_B/resource-managers?depth=2",
        "/servers/EG_C/resource-managers?depth=2",
    ], paths
    assert out["node"] == "NODE1"
    assert out["discovered_servers"] == {"NODE1": ["EG_A", "EG_B", "EG_C"]}, out
    assert out["count"] == 3
    assert out["requested_resource_managers"] == ["cache"]


def test_resource_inspect_sweep_uses_live_not_dump(monkeypatch):
    """The sweep must read the LIVE node, never `node_dump.csv`.

    `ACE_DEMO_RESTAPI` runs on NODE1 but is absent from the shipped dump. If
    discovery went through `known_servers()`/`_nodes_hosting()` it would be
    dropped, and "list all EGs" would quietly return an incomplete answer —
    worse than an error.
    """
    from server.ace_helpers import known_servers

    assert "ACE_DEMO_RESTAPI" not in known_servers("NODE1"), (
        "fixture assumption broken: the dump now lists ACE_DEMO_RESTAPI"
    )

    live = ["ACE_DEMO_CACHE", "ACE_DEMO_RESTAPI"]
    _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": live}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(node="NODE1", resource_managers=["cache"])))

    assert {s["server"] for s in out["servers"]} == set(live), out


def test_resource_inspect_sweep_discovers_nodes_when_both_omitted(monkeypatch):
    _stub_fetch_ace(
        monkeypatch, _sweep_handler({"NODE1": ["EG_A"], "NODE2": ["EG_B", "EG_C"]})
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(resource_managers=["cache"])))

    assert out["discovered_targets"] == ["NODE1", "NODE2"], out
    assert out["discovered_servers"] == {"NODE1": ["EG_A"], "NODE2": ["EG_B", "EG_C"]}
    assert out["count"] == 3
    assert {(s["node"], s["server"]) for s in out["servers"]} == {
        ("NODE1", "EG_A"),
        ("NODE2", "EG_B"),
        ("NODE2", "EG_C"),
    }, out


def test_resource_inspect_sweep_reports_discovery_failure(monkeypatch):
    """One unreachable node must not make a partial sweep look complete."""
    _stub_fetch_ace(
        monkeypatch,
        _sweep_handler({"NODE1": ["EG_A"], "NODE2": ["EG_B"]}, node_errors={"NODE2"}),
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(resource_managers=["cache"])))

    assert out["status"] == "success"
    assert out["discovered_servers"] == {"NODE1": ["EG_A"]}, out
    assert out["node_errors"] == [
        {"node": "NODE2", "servers_discovery_error": "NODE2 unreachable"}
    ], out
    assert {s["server"] for s in out["servers"]} == {"EG_A"}


def test_resource_inspect_sweep_all_nodes_failing_is_an_error(monkeypatch):
    _stub_fetch_ace(
        monkeypatch,
        _sweep_handler({"NODE1": ["EG_A"]}, node_errors={"NODE1"}),
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(node="NODE1")))

    assert out["status"] == "error", out
    assert "No integration servers could be listed" in out["message"]
    assert out["node_errors"][0]["node"] == "NODE1"


def test_resource_inspect_named_server_still_skips_discovery(monkeypatch):
    """A dump-known server costs exactly ONE call - no discovery, no sweep.

    Uses a real EG from `node_dump.csv` deliberately: name resolution answers
    from the dump with no HTTP, so this pins the hot path. A name the dump
    does NOT know is *expected* to fall through to live discovery - that is
    `..._live_only_server_falls_back_to_discovery` below.
    """
    paths = _stub_fetch_ace(
        monkeypatch, _sweep_handler({"NODE1": ["ACE_DEMO_CONNECTORS"]})
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(
            fn(
                servers=["ACE_DEMO_CONNECTORS"],
                node="NODE1",
                resource_managers=["cache"],
            )
        )
    )

    assert paths == [
        "/servers/ACE_DEMO_CONNECTORS/resource-managers?depth=2"
    ], paths
    assert out["server"] == "ACE_DEMO_CONNECTORS"
    assert "discovered_servers" not in out


# ---------------------------------------------------------------------------
# ace_resource_inspect — named-server resolution (live fallback + case/typos)
#
# Two defects found by probing the live nodes, both invisible to the tests
# above because those only ever used dump-known or wholly synthetic names:
#   1. An EG that is running but not yet in node_dump.csv (ACE_DEMO_RESTAPI)
#      was rejected as "not found on any configured integration node".
#   2. The ACE REST API is case-sensitive, so "ace_demo_cache" 404'd into a
#      bare "Endpoint not found".
# ---------------------------------------------------------------------------
LIVE_ONLY_EG = "ACE_DEMO_RESTAPI"


def test_fixture_live_only_eg_is_really_absent_from_the_dump():
    """Guards the premise of the tests below."""
    from server.ace_helpers import known_servers, resolve_server_name

    assert LIVE_ONLY_EG not in known_servers(), (
        "node_dump.csv now lists ACE_DEMO_RESTAPI - pick another live-only EG"
    )
    assert resolve_server_name(LIVE_ONLY_EG) is None


def test_resource_inspect_live_only_server_falls_back_to_discovery(monkeypatch):
    """A running EG missing from the dump resolves via the live node."""
    paths = _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": [LIVE_ONLY_EG]}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=[LIVE_ONLY_EG], node="NODE1",
                       resource_managers=["cache"]))
    )

    assert paths == [
        "/servers?depth=1",
        f"/servers/{LIVE_ONLY_EG}/resource-managers?depth=2",
    ], paths
    assert out["server"] == LIVE_ONLY_EG


def test_resource_inspect_live_only_server_without_a_node(monkeypatch):
    """The same EG with NO node scans the estate and finds its host."""
    _stub_fetch_ace(
        monkeypatch, _sweep_handler({"NODE1": [LIVE_ONLY_EG], "NODE2": ["OTHER"]})
    )
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=[LIVE_ONLY_EG], resource_managers=["cache"]))
    )

    assert out["status"] == "success", out
    assert out["discovered_nodes"] == ["NODE1"], out
    assert out["servers_resolved"] == [LIVE_ONLY_EG]
    assert out["count"] == 1


def test_resource_inspect_canonicalises_case_from_the_dump(monkeypatch):
    """Lowercase dump-known EG is corrected with NO discovery call."""
    paths = _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": []}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=["ace_demo_cache"], node="NODE1",
                       resource_managers=["cache"]))
    )

    assert paths == [
        "/servers/ACE_DEMO_CACHE/resource-managers?depth=2"
    ], paths
    assert out["server"] == "ACE_DEMO_CACHE"


def test_resource_inspect_canonicalises_case_from_the_live_node(monkeypatch):
    """Lowercase LIVE-ONLY EG is corrected against the live listing."""
    _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": [LIVE_ONLY_EG]}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=[LIVE_ONLY_EG.lower()], node="NODE1",
                       resource_managers=["cache"]))
    )

    assert out["server"] == LIVE_ONLY_EG, out


def test_resource_inspect_typo_returns_did_you_mean(monkeypatch):
    """An unresolvable EG suggests, and never fires a doomed REST call."""
    paths = _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": []}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(fn(servers=["ACE_DEMO_CONNECTOR"], node="NODE1"))
    )

    assert paths == ["/servers?depth=1"], paths  # no resource-managers call
    assert out["status"] == "error"
    assert out["unknown_servers"] == ["ACE_DEMO_CONNECTOR"]
    assert "ACE_DEMO_CONNECTORS" in out["did_you_mean"]["ACE_DEMO_CONNECTOR"]


def test_resource_inspect_nonsense_name_omits_empty_suggestions(monkeypatch):
    """No close match ⇒ `did_you_mean` absent, never an empty list."""
    _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": []}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(asyncio.run(fn(servers=["ZZZZZZZZ"], node="NODE1")))

    assert out["unknown_servers"] == ["ZZZZZZZZ"]
    assert "did_you_mean" not in out, out


def test_resource_inspect_partial_resolution_reports_both(monkeypatch):
    """One good EG + one bad name: inspect the good one, REPORT the bad one.

    Regression: the single-pair fast path returns the bare per-server
    envelope, which has nowhere to carry `unknown_servers` - so a mixed call
    used to answer for the good EG and silently drop the bad name.
    """
    _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": []}))
    fn = _tool("ace_resource_inspect")
    out = json.loads(
        asyncio.run(
            fn(servers=["ACE_DEMO_CACHE", "ZZZZZZZZ"], node="NODE1",
               resource_managers=["cache"])
        )
    )

    assert out["status"] == "success", out
    assert out["unknown_servers"] == ["ZZZZZZZZ"], out
    assert out["servers_resolved"] == ["ACE_DEMO_CACHE"], out
    assert [x["server"] for x in out["servers"]] == ["ACE_DEMO_CACHE"], out


def test_resource_inspect_dedups_two_spellings_of_one_server(monkeypatch):
    """"ACE_DEMO_CACHE" and "ace_demo_cache" must be inspected once."""
    paths = _stub_fetch_ace(monkeypatch, _sweep_handler({"NODE1": []}))
    fn = _tool("ace_resource_inspect")
    asyncio.run(
        fn(servers=["ACE_DEMO_CACHE", "ace_demo_cache"], node="NODE1",
           resource_managers=["cache"])
    )

    assert paths == [
        "/servers/ACE_DEMO_CACHE/resource-managers?depth=2"
    ], paths


def test_resource_inspect_unknown_manager_omits_empty_did_you_mean(monkeypatch):
    """Envelope level: an unmatchable manager reports plainly, no empty list.

    Mirrors `test_resource_inspect_nonsense_name_omits_empty_suggestions`,
    which guards the same rule for EG names.
    """
    _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    env = asyncio.run(
        __import__("server.composite_tools", fromlist=["x"])._resource_inspect_one(
            "NODE1", "EG1", ["zzzz"]
        )
    )

    assert env["unknown_resource_managers"] == ["zzzz"], env
    assert "did_you_mean" not in env, env
    assert env["resource_managers"] == []


def test_resource_inspect_unknown_manager_keeps_real_suggestions(monkeypatch):
    from server.composite_tools import _resource_inspect_one

    _stub_fetch_ace(monkeypatch, lambda p: _rm_payload(_CACHE_ENTRIES))
    env = asyncio.run(_resource_inspect_one("NODE1", "EG1", ["glob4l-cache"]))

    # Close enough to auto-resolve, so it lands in resource_managers.
    assert [r["name"] for r in env["resource_managers"]] == ["global-cache"], env
    assert "unknown_resource_managers" not in env
