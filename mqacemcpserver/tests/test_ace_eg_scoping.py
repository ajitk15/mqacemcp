"""EG-scoping guarantees for the offline ACE path.

The failure these lock down: asking "what applications run under EG
ACE_DEMO_CONNECTORS" used to return rows belonging to other execution groups,
because every offline answer went through a substring sweep over the whole
concatenated dump row. The dump's BIP text is now parsed into real
eg/application/flow columns and matched by equality.

Ground truth is the shipped `resources/node_dump.csv`: ACE_DEMO_CONNECTORS
occupies lines 15-34 (NODE1) and 104-123 (NODE2) and holds exactly three
applications.
"""
from __future__ import annotations

import json

import mqacemcpserver  # noqa: F401  — imports register the tools
from server.ace_helpers import (
    _parse_resource,
    dump_rows,
    known_servers,
    nodes_hosting_application,
    nodes_hosting_server,
    resolve_server_name,
    server_inventory,
    suggest_servers,
)

CONNECTORS = "ACE_DEMO_CONNECTORS"
CONNECTOR_APPS = {"AmazonS3", "ACE_Salesforce_Leads", "HTTP_Multiple_Requests"}
OTHER_EGS = ["ACE_DEMO_CACHE", "ACE_DEMO_MESSAGING", "ACE_DEMO_TRANSFORM"]


def _tool(name: str):
    return mqacemcpserver.mcp._tool_manager._tools[name].fn


# ---------------------------------------------------------------------------
# BIP parsing
# ---------------------------------------------------------------------------
def test_parse_integration_server_line():
    p = _parse_resource(
        "BIP1286I: Integration server 'ACE_DEMO_CONNECTORS' on integration "
        "node 'NODE1' is running."
    )
    assert p["resource_kind"] == "server"
    assert p["eg"] == CONNECTORS
    assert p["state"] == "running"


def test_parse_application_line():
    p = _parse_resource(
        "BIP1275I: Application 'AmazonS3' on integration server "
        "'ACE_DEMO_CONNECTORS' is running."
    )
    assert p["resource_kind"] == "application"
    assert p["eg"] == CONNECTORS
    assert p["application"] == "AmazonS3"
    assert p["state"] == "running"


def test_parse_stopped_message_flow_line():
    p = _parse_resource(
        "BIP1278I: Message flow 'CreateItem' on integration server "
        "'ACE_DEMO_CONNECTORS' is stopped. (Application 'AmazonS3', Library '')"
    )
    assert p["resource_kind"] == "flow"
    assert p["eg"] == CONNECTORS
    assert p["flow"] == "CreateItem"
    assert p["application"] == "AmazonS3"
    assert p["state"] == "stopped"


def test_parse_file_and_policy_lines():
    f = _parse_resource(
        "BIP1299I: File 'main_Compute.esql' is deployed to integration server "
        "'ACE_DEMO_CONNECTORS'. (Application 'HTTP_Multiple_Requests', Library '')"
    )
    assert f["resource_kind"] == "file"
    assert f["eg"] == CONNECTORS
    assert f["file"] == "main_Compute.esql"

    p = _parse_resource(
        "BIP1391I: Policy 'AmazonS31' type 'amazons3' is deployed as "
        "'awss3/AmazonS31.policyxml' to integration server 'ACE_DEMO_CONNECTORS'."
    )
    assert p["resource_kind"] == "policy"
    assert p["eg"] == CONNECTORS
    assert p["policy"] == "AmazonS31"


def test_parse_unrecognised_line_yields_empty_fields_but_does_not_raise():
    p = _parse_resource("something entirely unexpected")
    assert p["eg"] == ""
    assert p["resource_kind"] == ""
    assert p["application"] == ""


def test_unparseable_rows_are_not_dropped_from_the_frame():
    """A row the parser cannot understand must still be searchable."""
    from server.ace_helpers import load_node_dump

    df = load_node_dump()
    assert not df.empty
    assert "eg" in df.columns
    # Every source row survives parsing.
    assert len(df) == len(df["resource"])


# ---------------------------------------------------------------------------
# Exact lookups
# ---------------------------------------------------------------------------
def test_known_servers_lists_the_execution_groups():
    servers = known_servers()
    assert CONNECTORS in servers
    for eg in OTHER_EGS:
        assert eg in servers


def test_resolve_server_name_is_exact_and_case_insensitive():
    assert resolve_server_name("ace_demo_connectors") == CONNECTORS
    # A near miss is NOT a resolution — that is the whole point.
    assert resolve_server_name("ACE_DEMO_CONNECTOR") is None
    assert resolve_server_name("CONNECTORS") is None


def test_nodes_hosting_server_is_exact():
    assert nodes_hosting_server(CONNECTORS) == ["NODE1", "NODE2"]
    # A server that does not exist hosts nothing, even though the string is a
    # prefix of a real one.
    assert nodes_hosting_server("ACE_DEMO_CONNECTOR") == []


def test_nodes_hosting_application_matches_the_application_column():
    assert nodes_hosting_application("AmazonS3") == ["NODE1", "NODE2"]
    # 'AmazonS31' is a POLICY, not an application.
    assert nodes_hosting_application("AmazonS31") == []


def test_server_inventory_returns_exactly_the_three_applications():
    inv = server_inventory(CONNECTORS)
    assert inv is not None
    assert inv["server"] == CONNECTORS
    assert inv["nodes"] == ["NODE1", "NODE2"]
    assert {a["name"] for a in inv["applications"]} == CONNECTOR_APPS
    assert inv["application_count"] == 3


def test_server_inventory_carries_flow_state():
    inv = server_inventory(CONNECTORS, node="NODE1")
    apps = {a["name"]: a for a in inv["applications"]}
    flows = {f["name"]: f["state"] for f in apps["AmazonS3"]["flows"]}
    assert flows == {"CreateItem": "stopped"}
    assert apps["HTTP_Multiple_Requests"]["flows"][0]["state"] == "running"


def test_server_inventory_unknown_eg_returns_none():
    assert server_inventory("ACE_DEMO_CONNECTOR") is None
    assert server_inventory("NOPE") is None


def test_suggest_servers_offers_the_real_name():
    assert CONNECTORS in suggest_servers("ACE_DEMO_CONNECTOR")


def test_dump_rows_scopes_to_one_node():
    assert len(dump_rows(server=CONNECTORS, node="NODE1")) == 20
    assert len(dump_rows(server=CONNECTORS)) == 40


# ---------------------------------------------------------------------------
# ace_search — the tool that produced the wrong answer
# ---------------------------------------------------------------------------
def test_ace_search_scopes_an_eg_name_exactly():
    out = json.loads(_tool("ace_search")(search_strings=[CONNECTORS], scope="dump"))
    assert out["status"] == "success"
    assert out["match_kind"] == "exact-eg"

    inv = out["servers"][0]
    assert {a["name"] for a in inv["applications"]} == CONNECTOR_APPS


def test_ace_search_eg_scope_leaks_no_other_execution_group():
    """The reported bug: rows from EGs the user never asked about."""
    out = json.loads(_tool("ace_search")(search_strings=[CONNECTORS], scope="dump"))
    assert out["dump_matches"]
    for row in out["dump_matches"]:
        assert CONNECTORS in row["resource"], row
        for other in OTHER_EGS:
            assert other not in row["resource"], row


def test_ace_search_eg_name_wins_over_loose_terms():
    """`["ACE_DEMO_CONNECTORS", "Application"]` used to OR together and return
    every BIP1275I row for every EG. The EG scoping must win."""
    out = json.loads(
        _tool("ace_search")(
            search_strings=[CONNECTORS, "Application"], scope="dump"
        )
    )
    assert out["match_kind"] == "exact-eg"
    assert out["ignored_search_strings"] == ["Application"]
    for row in out["dump_matches"]:
        assert CONNECTORS in row["resource"], row


def test_ace_search_application_scope_excludes_the_prefix_policy():
    """'AmazonS3' must not drag in Policy 'AmazonS31'."""
    out = json.loads(
        _tool("ace_search")(
            search_strings=[CONNECTORS], application="AmazonS3", scope="dump"
        )
    )
    assert out["match_kind"] == "exact-eg"
    assert [a["name"] for a in out["servers"][0]["applications"]] == ["AmazonS3"]
    for row in out["dump_matches"]:
        assert "AmazonS31" not in row["resource"], row


def test_ace_search_unknown_server_is_not_found_with_suggestions():
    out = json.loads(
        _tool("ace_search")(search_strings=[""], server="ACE_DEMO_CONNECTOR")
    )
    assert out["status"] == "not_found"
    assert out["server"] == "ACE_DEMO_CONNECTOR"
    assert CONNECTORS in out["did_you_mean"]
    # Crucially: no rows at all, rather than someone else's.
    assert "dump_matches" not in out


def test_ace_search_substring_path_still_works_for_free_text():
    out = json.loads(_tool("ace_search")(search_strings=["BIP"], scope="dump"))
    assert out["match_kind"] == "substring"
    assert out["dump_matches"]


def test_ace_search_near_miss_surfaces_did_you_mean():
    """A free-text term that is nearly an EG name still substring-searches, but
    the correction is surfaced so the caller can see the wide result coming."""
    out = json.loads(
        _tool("ace_search")(search_strings=["ACE_DEMO_CONNECTOR"], scope="dump")
    )
    assert out["match_kind"] == "substring"
    assert CONNECTORS in out["did_you_mean"]["ACE_DEMO_CONNECTOR"]


# ---------------------------------------------------------------------------
# ace_server_explore — node discovery must intersect, not union
# ---------------------------------------------------------------------------
def test_server_explore_intersects_server_and_application():
    """CX6: ACE_MQ_group_messages lives on ACE_DEMO_MESSAGING, not on
    ACE_DEMO_CONNECTORS. Asking for it on CONNECTORS must not resolve nodes
    via the application name."""
    from server.composite_tools import _nodes_hosting

    assert _nodes_hosting([CONNECTORS]) == ["NODE1", "NODE2"]
    # The old code searched for any row mentioning the name; a policy or file
    # named after something must not make its node a "host".
    assert _nodes_hosting(["AmazonS31"]) == []


# ---------------------------------------------------------------------------
# Extract-time provenance — the regression that started this
# ---------------------------------------------------------------------------
def test_dump_rows_carry_no_date():
    """A per-row extract time got reported as an EG's restart time.

    Every row of node_dump.csv carries the SAME extractedat (it is one run), so
    a date on a row described the file, not the row — and sitting beside
    "...is running." it read as when the thing started running. Rows now carry
    only per-row facts; the extract time is envelope metadata.
    """
    rows = dump_rows(server=CONNECTORS)
    assert rows
    for row in rows:
        assert set(row) == {"hostname", "node", "resource"}, row


def test_ace_search_reports_the_extract_time_once():
    """CX10 still answerable: the provenance question is about the FILE."""
    out = json.loads(_tool("ace_search")(search_strings=[CONNECTORS], scope="dump"))
    assert out["extractedat"] == "2026-06-25 10:30:19"
    assert "node_dump.csv" in out["data_source"]
    # The shipped extract is a single instant, so no range is reported.
    assert "extractedat_range" not in out


def test_extract_date_is_not_searchable():
    """`extractedat` sat in the search haystack, so any part of that date
    matched all 178 rows. It is excluded now."""
    out = json.loads(_tool("ace_search")(search_strings=["2026-06"], scope="dump"))
    assert out["dump_matches"] == []


def test_malformed_extract_is_rejected_at_load(tmp_path, monkeypatch):
    """A botched daily swap must not take the ACE tools down.

    The loader used to accept any parseable CSV, so a dump missing `resource`
    (or still carrying the old timestamp|host|status headers) sailed through
    load and surfaced as a KeyError inside whichever tool was called next.
    Rejecting it here is what lets CsvCache keep serving the last good extract.
    """
    from server import ace_helpers

    for header, row in (
        ("extractedat|hostname|node", "2026-06-25 10:30:19|localhost|NODE1"),
        ("timestamp|host|status", "2026-06-25 10:30:19|localhost|whatever"),
    ):
        bad = tmp_path / "node_dump.csv"
        bad.write_text(f"{header}\n{row}", encoding="utf-8")
        monkeypatch.setattr(ace_helpers, "ACE_NODE_DUMP_PATH", bad)
        assert ace_helpers._load_node_dump_from_disk() is None, header


def test_extract_window_survives_a_missing_or_unparseable_date():
    """`extractedat` is optional metadata — never a reason to fail a call."""
    import pandas as pd
    from server import ace_helpers

    for frame in (
        pd.DataFrame({"hostname": ["h"], "node": ["N"], "resource": ["r"]}),
        pd.DataFrame({"extractedat": [pd.NaT], "hostname": ["h"],
                      "node": ["N"], "resource": ["r"]}),
    ):
        assert ace_helpers._add_structured_columns(frame.copy()) is not None


# ---------------------------------------------------------------------------
# An APPLICATION passed where an EG is expected (CX18)
# ---------------------------------------------------------------------------
APP_ON_CONNECTORS = "ACE_Salesforce_Leads"


def test_servers_hosting_application_maps_app_to_its_eg():
    from server.ace_helpers import servers_hosting_application

    assert servers_hosting_application(APP_ON_CONNECTORS) == [CONNECTORS]
    assert servers_hosting_application("no_such_thing") == []
    # An EG name is not an application, so this must stay empty.
    assert servers_hosting_application(CONNECTORS) == []


def _run(coro):
    import asyncio

    return json.loads(asyncio.run(coro))


def test_resource_inspect_answers_an_application_against_its_hosting_eg():
    """CX18: "does debug enabled for EG ACE_Salesforce_Leads".

    That name is an application on ACE_DEMO_CONNECTORS, and the setting asked
    about is EG-level. The tool used to answer "not found on any configured
    integration node" — which a single-call client can only relay as "it does
    not exist", since it cannot chain a second lookup. It now substitutes the
    hosting EG and answers the question that was meant, keeping the correction
    visible so the reply can say whose settings these are.
    """
    out = _run(
        _tool("ace_resource_inspect")(
            servers=[APP_ON_CONNECTORS], resource_managers=["jvm"]
        )
    )
    assert out["status"] == "success"
    assert out["substituted_applications"][APP_ON_CONNECTORS] == {
        "hosted_on_servers": [CONNECTORS],
        "nodes": ["NODE1", "NODE2"],
    }
    assert "APPLICATIONS" in out["substitution_note"]
    # The EG actually inspected is the host, never the application name. The
    # resource-manager VALUES are live, and conftest pins the allow-list to
    # lod/loq/lot so localhost is refused here by design — jvmDebugPort itself
    # is covered by clients/smoke_test_http.py and the chatbot suite.
    assert {s["server"] for s in out["servers"]} == {CONNECTORS}
    assert out["servers_resolved"] == [CONNECTORS]


def test_server_explore_names_the_hosting_eg_for_an_application():
    """Same trap, different failure: `_nodes_hosting` matches the application
    column, so the hosting nodes resolved and the call went on to
    `/servers/<app>/applications`, which 404s on every node."""
    out = _run(_tool("ace_server_explore")(servers=[APP_ON_CONNECTORS]))
    assert out["status"] == "error"
    assert out["is_application_not_server"][APP_ON_CONNECTORS][
        "hosted_on_servers"
    ] == [CONNECTORS]


def test_a_real_eg_is_never_mistaken_for_an_application():
    """The guard must not fire for genuine EG names or near-miss typos."""
    out = _run(_tool("ace_resource_inspect")(servers=["ACE_DEMO_CONNECTOR"]))
    assert "is_application_not_server" not in out
    assert CONNECTORS in out["did_you_mean"]["ACE_DEMO_CONNECTOR"]


# ---------------------------------------------------------------------------
# ace_search node scoping
# ---------------------------------------------------------------------------
GROUP_APP = "ACE_MQ_group_messages"


def test_ace_search_node_filters_the_dump():
    """"... on NODE1" had nowhere to go: ace_search took server/application
    but no node, so the whole question could not be expressed."""
    both = json.loads(_tool("ace_search")(search_strings=[GROUP_APP], scope="dump"))
    assert {r["node"] for r in both["dump_matches"]} == {"NODE1", "NODE2"}

    for node in ("NODE1", "NODE2"):
        out = json.loads(
            _tool("ace_search")(search_strings=[GROUP_APP], scope="dump", node=node)
        )
        assert out["node"] == node
        assert {r["node"] for r in out["dump_matches"]} == {node}
        assert len(out["dump_matches"]) < len(both["dump_matches"])


def test_ace_search_moves_a_node_name_out_of_the_server_argument():
    """The commonest misuse, and the cause of a lost answer: with no `node`
    parameter the only slot for "NODE1" was `server`, which returned
    not_found. The intent is unambiguous, so honour it and say so."""
    out = json.loads(
        _tool("ace_search")(search_strings=[GROUP_APP], scope="dump", server="NODE1")
    )
    assert out["status"] == "success"
    assert out["node"] == "NODE1"
    assert "server" not in out
    assert "NODE" in out["corrected_arguments"]
    assert {r["node"] for r in out["dump_matches"]} == {"NODE1"}


def test_a_real_eg_in_the_server_argument_is_not_rewritten():
    out = json.loads(
        _tool("ace_search")(search_strings=[CONNECTORS], scope="dump", server=CONNECTORS)
    )
    assert out["server"] == CONNECTORS
    assert "corrected_arguments" not in out
    assert "node" not in out
