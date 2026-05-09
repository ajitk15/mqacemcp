"""Tests for offline ACE paths."""
from __future__ import annotations

import json

from server.ace_helpers import search_node_dump


def test_search_node_dump_finds_sample_app():
    results = search_node_dump("snaplogic1")
    # Sample node_dump.csv has 8 BIP lines mentioning snaplogic1
    assert len(results) > 0
    for r in results:
        assert "node" in r and "host" in r and "status" in r


def test_search_node_dump_returns_empty_for_misses():
    assert search_node_dump("definitely-not-in-the-dump-zzz") == []


def test_list_ace_nodes_returns_configured_nodes():
    import mqacemcpserver  # noqa: F401
    import asyncio

    fn = mqacemcpserver.mcp._tool_manager._tools["list_ace_nodes"].fn
    payload = json.loads(asyncio.run(fn()))
    assert payload["status"] == "success"
    nodes = {n["node"] for n in payload["configured_nodes"]}
    assert "NODE01" in nodes
