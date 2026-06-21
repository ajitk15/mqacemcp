"""Offline coverage for `get_queue_depth`'s alias -> remote -> local chain.

No real HTTP — `run_mqsc_raw`, manifest discovery, and the QM->host lookup are
all stubbed. The regression under test: an alias whose TARGET is a QREMOTE must
resolve through the remote queue onto its destination QM, not be reported as a
"QLOCAL not found".
"""
from __future__ import annotations

import asyncio

import mqacemcpserver  # noqa: F401 — importing registers the tools
from server import mq_tools


def _tool(name: str):
    """Return the registered callable for a tool name."""
    return mqacemcpserver.mcp._tool_manager._tools[name].fn


def _chain_stub(captured: list[tuple[str, str]]):
    """Emulate QA.IN.APP2 (alias) -> QR.IN.APP2 (remote) -> QA.IN.APP2 (local)."""

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
        if "DISPLAY QLOCAL(QA.IN.APP2) CURDEPTH" in u:
            return "QUEUE(QA.IN.APP2) TYPE(QLOCAL) CURDEPTH(7)"
        return f"[stub] {mqsc}"

    return fake


def test_get_queue_depth_alias_to_remote_chain(monkeypatch):
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        mq_tools,
        "search_objects_structured",
        lambda *a, **k: [
            {
                "qmgr": "MQQMGR2",
                "hostname": "loq-mq01",
                "object_type": "QALIAS",
                "restricted": False,
            }
        ],
    )
    monkeypatch.setattr(mq_tools, "run_mqsc_raw", _chain_stub(captured))
    # MQQMGR1 resolves to an allow-listed host so the chain is chased onto it.
    monkeypatch.setattr(mq_tools, "_resolve_qm_host", lambda qmgr: "loq-mq02")

    fn = _tool("get_queue_depth")
    result = asyncio.run(fn(queue_name="QA.IN.APP2"))

    assert (
        "QA.IN.APP2(MQQMGR2) --> QR.IN.APP2(MQQMGR2) --> QA.IN.APP2(MQQMGR1)"
        in result
    ), result
    # The old bug: querying the remote queue as a local queue.
    assert not any("DISPLAY QLOCAL(QR.IN.APP2)" in m for _, m in captured), captured
    # The destination depth on MQQMGR1 must be fetched.
    assert ("MQQMGR1", "DISPLAY QLOCAL(QA.IN.APP2) CURDEPTH") in captured, captured
    assert "CURDEPTH(7)" in result, result
