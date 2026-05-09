"""Tests for offline MQ paths (manifest search + runmqsc allow-list bypass fix)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import mqacemcpserver  # noqa: F401 — registers tools


@pytest.fixture
def find_mq_object():
    return mqacemcpserver.mcp._tool_manager._tools["find_mq_object"].fn


@pytest.fixture
def runmqsc():
    return mqacemcpserver.mcp._tool_manager._tools["runmqsc"].fn


def test_find_mq_object_returns_hits_from_manifest(find_mq_object):
    out = find_mq_object("QL.IN.APP1")
    # Sample data hosts QL.IN.APP1 on both lopalhost (restricted) and lodalhost (allowed)
    assert "MQQMGR1" in out
    assert "lodalhost" in out
    assert "[RESTRICTED: lopalhost]" in out


def test_runmqsc_rejects_unknown_qmgr_without_hostname(runmqsc):
    """Allow-list bypass must NOT happen — unknown QM + no hostname = rejected.

    Mocks mq_post so that *any* attempt to fire an HTTP request fails the test.
    """
    with patch(
        "server.mq_helpers.mq_post",
        side_effect=AssertionError("HTTP call should NOT be attempted"),
    ):
        out = asyncio.run(runmqsc("DEFINITELY_NOT_A_REAL_QM", "DISPLAY QMGR ALL"))
    assert "not in the manifest" in out
    assert "DEFINITELY_NOT_A_REAL_QM" in out


def test_runmqsc_blocks_modification_commands(runmqsc):
    out = asyncio.run(runmqsc("ANY_QM", "ALTER QLOCAL(QL.X) MAXDEPTH(99)"))
    assert "Modification requests are not permitted" in out
