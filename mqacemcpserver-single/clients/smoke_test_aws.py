"""SSE smoke-test client for mqacemcpserver-single — AWS environment.

Same scaffolding as clients/smoke_test.py, but the call table is populated
from the AWS extract files in C:\\Users\\ajitk\\Downloads\\aws:

    qmgr_dump.csv   — MQ queue managers, queues, channels, aliases
    node_dump.csv   — ACE integration nodes, servers, applications, flows
    node_config.csv — ACE node -> host:port mapping

MQ topology (qmname @ host):
    MQNODE1 @ ip-10-0-1-130.ec2.internal   (cluster member)
    MQNODE2 @ ip-10-0-1-13.ec2.internal    (cluster member)
    MQREPO1 @ ip-10-0-1-169.ec2.internal   (full repository)
    MQREPO2 @ ip-10-0-1-130.ec2.internal   (full repository)
    QM1     @ ip-10-0-1-169.ec2.internal   (standalone dev QM)

ACE topology (node @ host:port):
    NODE1   @ ip-10-0-1-130.ec2.internal:4414
    NODE2   @ ip-10-0-1-13.ec2.internal:4414
    integration servers on each node:
        ACE_DEMO_CACHE, ACE_DEMO_CONNECTORS, ACE_DEMO_MESSAGING, ACE_DEMO_TRANSFORM

Every non-SYSTEM MQ object and every ACE node/server/application from the
extract is exercised below. Object resolution (qmname -> host, node -> host)
is the server's job via its loaded manifests; this client only supplies names.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import urllib3
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

MCP_AUTH_USER = os.getenv("MCP_AUTH_USER", "")
MCP_AUTH_PASSWORD = os.getenv("MCP_AUTH_PASSWORD", "")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = os.getenv("MCP_PORT", "8443")
MCP_TLS_CERT = os.getenv("MCP_TLS_CERT", "")
MCP_TLS_KEY = os.getenv("MCP_TLS_KEY", "")

if MCP_HOST in ("", "0.0.0.0"):
    MCP_HOST = "127.0.0.1"

_scheme = "https" if (MCP_TLS_CERT and MCP_TLS_KEY) else "http"
SSE_URL = os.getenv("MCP_REMOTE_SERVER_URL", f"{_scheme}://{MCP_HOST}:{MCP_PORT}/sse")


def _make_insecure_httpx_client(headers=None, timeout=None, auth=None):
    kwargs = {"follow_redirects": True, "verify": False}
    kwargs["timeout"] = timeout if timeout is not None else httpx.Timeout(30.0, read=300.0)
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def heading(text):
    bar = "=" * 64
    print(f"\n{bar}\n  {text}\n{bar}")


def preview(text, limit=12):
    """Print an indented preview of `text`. `limit=None` prints every line."""
    lines = text.split("\n")
    shown = lines if limit is None else lines[:limit]
    for line in shown:
        print(f"    {line}")
    if limit is not None and len(lines) > limit:
        print(f"    ... ({len(lines) - limit} more lines)")


EXPECTED_TOOLS = {
    "mq_queue_inspect", "mq_channel_inspect", "mq_host_overview",
    "ace_node_overview", "ace_server_explore", "ace_search",
    "get_cert_details",
}

# Non-SYSTEM application queues that exist identically on MQNODE1 and MQNODE2.
NODE_APP_QUEUES = [
    "QL.INPUT", "QL.INPUT.LFT", "QL.LOCAL", "QL.OUT", "QL.OUT.SPLIT",
    "QL.OUT1", "QL.OUT2", "QL.SOURCE", "QL.TARGET.LAPTOP", "QL.TARGET.MONITOR",
    "QL.TARGET.MOUSE", "QL.TARGET.SPLIT", "QL.TARGET.SPLIT_IN", "QL.TARGET.SPLIT_OUT",
]

# All four integration servers present on both NODE1 and NODE2.
ACE_SERVERS = ["ACE_DEMO_CACHE", "ACE_DEMO_CONNECTORS", "ACE_DEMO_MESSAGING", "ACE_DEMO_TRANSFORM"]

CALLS = [
    # =========================================================================
    # mq_queue_inspect — every non-SYSTEM queue across all five queue managers
    # =========================================================================
    # MQNODE1 application queues (multi-target: all 14 in one call)
    ("mq_queue_inspect", {"queue_names": NODE_APP_QUEUES, "qmgr_name": "MQNODE1"}, "live"),
    # MQNODE2 application queues (same set, second cluster member)
    ("mq_queue_inspect", {"queue_names": NODE_APP_QUEUES, "qmgr_name": "MQNODE2"}, "live"),
    # Single-queue depth/attribute fetch
    ("mq_queue_inspect", {"queue_names": ["QL.INPUT"], "qmgr_name": "MQNODE1"}, "live"),
    ("mq_queue_inspect", {"queue_names": ["QL.OUT.SPLIT"], "qmgr_name": "MQNODE2"}, "live"),
    # MQREPO1 admin queues + alias (alias should follow TARGET to the QLOCAL)
    ("mq_queue_inspect", {"queue_names": ["QL.ADMIN.REPLY", "QL.ADMIN.REQUEST"], "qmgr_name": "MQREPO1"}, "live"),
    ("mq_queue_inspect", {"queue_names": ["QL.ADMIN.REQUEST.ALIAS"], "qmgr_name": "MQREPO1"}, "live"),  # QALIAS -> QL.ADMIN.REQUEST
    # MQREPO2 repo queues + alias
    ("mq_queue_inspect", {"queue_names": ["QL.REPO.AUDIT", "QL.REPO.BACKUP"], "qmgr_name": "MQREPO2"}, "live"),
    ("mq_queue_inspect", {"queue_names": ["QL.REPO.BACKUP.ALIAS"], "qmgr_name": "MQREPO2"}, "live"),    # QALIAS -> QL.REPO.BACKUP
    # QM1 standalone dev queue
    ("mq_queue_inspect", {"queue_names": ["DEV.QUEUE.1"], "qmgr_name": "QM1"}, "live"),
    # Manifest path (no qmgr_name) — resolves QM hosting the queue
    ("mq_queue_inspect", {"queue_names": ["QL.INPUT"]}, "live"),
    # Negative
    ("mq_queue_inspect", {"queue_names": ["NOPE.DOES.NOT.EXIST"], "qmgr_name": "MQNODE1"}, "expect_not_found"),

    # =========================================================================
    # mq_channel_inspect — every non-SYSTEM channel across all five QMs
    # =========================================================================
    # Cluster channels per QM (CLUSRCVR + CLUSSDR)
    ("mq_channel_inspect", {"channel_names": ["MQNODE1.CLUSRCVR", "MQNODE1.CLUSSDR"], "qmgr_name": "MQNODE1"}, "live"),
    ("mq_channel_inspect", {"channel_names": ["MQNODE2.CLUSRCVR", "MQNODE2.CLUSSDR"], "qmgr_name": "MQNODE2"}, "live"),
    ("mq_channel_inspect", {"channel_names": ["MQREPO1.CLUSRCVR", "MQREPO1.CLUSSDR"], "qmgr_name": "MQREPO1"}, "live"),
    ("mq_channel_inspect", {"channel_names": ["MQREPO2.CLUSRCVR", "MQREPO2.CLUSSDR"], "qmgr_name": "MQREPO2"}, "live"),
    # QM1 SVRCONN channels (client app + admin)
    ("mq_channel_inspect", {"channel_names": ["DEV.ADMIN.SVRCONN", "DEV.APP.SVRCONN"], "qmgr_name": "QM1"}, "live"),
    # MULTI-TARGET mixing a real channel with an unknown one
    ("mq_channel_inspect", {"channel_names": ["MQNODE1.CLUSRCVR", "CH.UNKNOWN.XYZ"], "qmgr_name": "MQNODE1"}, "live"),
    # Negative
    ("mq_channel_inspect", {"channel_names": ["CH.UNKNOWN.XYZ"], "qmgr_name": "MQNODE1"}, "expect_not_found"),

    # =========================================================================
    # mq_host_overview — dspmq/dspmqver per QM + read-only MQSC probes
    # =========================================================================
    ("mq_host_overview", {}, "live"),                                                                 # default MQ_URL_BASE
    ("mq_host_overview", {"qmgr_names": ["MQNODE1", "MQNODE2", "MQREPO1", "MQREPO2", "QM1"]}, "live"), # MULTI-TARGET: all five QMs
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QMGR ALL"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.INPUT) ALL"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQNODE2"], "mqsc_command": "DISPLAY QLOCAL(QL.OUT) MAXDEPTH CURDEPTH QDEPTHHI QDEPTHLO"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.INPUT) CRDATE CRTIME"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQREPO1"], "mqsc_command": "DISPLAY QMGR DEADQ DEFXMITQ MAXMSGL MAXHANDS CCSID"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY CHANNEL(MQNODE1.CLUSRCVR) ALL"}, "live"),
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY CLUSQMGR(*) QMNAME CHANNEL STATUS"}, "live"),  # cluster membership
    ("mq_host_overview", {"qmgr_names": ["QM1"], "mqsc_command": "DISPLAY CHANNEL(DEV.APP.SVRCONN) CHLTYPE MCAUSER"}, "live"),
    # Read-only enforcement: modification verb must be blocked
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DEFINE QLOCAL(SMOKE.BLOCK.TEST)"}, "expect_blocked"),
    # Explicit host with no qmgr_name -> warn that MQSC needs a QM target
    ("mq_host_overview", {"hostnames": ["ip-10-0-1-130.ec2.internal"], "mqsc_command": "DISPLAY QMGR"}, "expect_warn_no_qmgr"),

    # =========================================================================
    # ace_node_overview — both nodes + every integration server
    # =========================================================================
    ("ace_node_overview", {"nodes": ["NODE1"]}, "live"),
    ("ace_node_overview", {"nodes": ["NODE2"]}, "live"),
    ("ace_node_overview", {"nodes": ["NODE1", "NODE2"]}, "live"),     # MULTI-TARGET: both nodes, one call
    ("ace_node_overview", {"nodes": ["GHOST.NODE"]}, "expect_error_envelope"),

    # =========================================================================
    # ace_server_explore — every server on each node, plus application scoping
    # =========================================================================
    ("ace_server_explore", {"node": "NODE1", "servers": ACE_SERVERS}, "live"),   # MULTI-TARGET: all 4 servers
    ("ace_server_explore", {"node": "NODE2", "servers": ACE_SERVERS}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CACHE"]}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CACHE"], "application": "ACE_flow_Cache"}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CONNECTORS"], "application": "AmazonS3"}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CONNECTORS"], "application": "ACE_Salesforce_Leads"}, "live"),
    ("ace_server_explore", {"node": "NODE2", "servers": ["ACE_DEMO_MESSAGING"], "application": "ACE_multi_dest_mq"}, "live"),
    ("ace_server_explore", {"node": "NODE2", "servers": ["ACE_DEMO_MESSAGING"], "application": "IBMACEJMSInput"}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_TRANSFORM"], "application": "ACE_xml2json"}, "live"),
    ("ace_server_explore", {"node": "NODE2", "servers": ["GHOST.SERVER"]}, "expect_error_envelope"),

    # =========================================================================
    # ace_search — offline scans over the node manifests
    # =========================================================================
    ("ace_search", {"search_strings": [""], "scope": "nodes"}, "offline"),
    ("ace_search", {"search_strings": ["ACE_DEMO"], "scope": "dump"}, "offline"),
    ("ace_search", {"search_strings": ["AmazonS3", "Salesforce"], "scope": "dump"}, "offline"),   # MULTI-TARGET: match either
    ("ace_search", {"search_strings": ["BIP1286I"], "scope": "dump"}, "offline"),                  # integration-server status lines
    ("ace_search", {"search_strings": [""]}, "offline"),                                           # default scope = all
    ("ace_search", {"search_strings": ["x"], "scope": "bogus"}, "expect_error_envelope"),

    # =========================================================================
    # get_cert_details — offline cert manifest lookups (depends on cert_dump.csv)
    # =========================================================================
    ("get_cert_details", {"search_strings": ["MQNODE1"]}, "offline"),
    ("get_cert_details", {"search_strings": ["ip-10-0-1-130"]}, "offline"),
    ("get_cert_details", {"search_strings": ["no-such-cert-anywhere"]}, "offline"),
]


# Category selectors for the optional CLI filter (see select_calls).
_CATEGORY = {
    "mq": lambda n: n.startswith("mq_"),
    "ace": lambda n: n.startswith("ace_"),
    "cert": lambda n: "cert" in n,
}


def select_calls(calls, selectors):
    """Filter CALLS by CLI selectors.

    Each selector is either a category keyword ('mq', 'ace', 'cert') or an
    exact / substring tool name (e.g. 'mq_queue_inspect', 'overview'). A call
    is kept if it matches ANY selector. Empty selectors -> run everything.
    """
    if not selectors:
        return list(calls)

    def matches(name, sel):
        if sel in _CATEGORY:
            return _CATEGORY[sel](name)
        return sel == name or sel in name

    return [c for c in calls if any(matches(c[0], s) for s in selectors)]


def classify(text, mode):
    s = text.lstrip()
    is_warn = s.startswith("⚠️") or s.startswith("⚠")
    is_err = s.startswith("❌") or s.startswith("🚫")
    parsed_status = None
    if s.startswith("{"):
        try:
            parsed_status = json.loads(s).get("status")
        except Exception:
            pass

    if mode == "expect_not_found":
        if is_err and "not found" in s.lower():
            return "pass", ""
        return "fail", "expected '❌ ... not found ...' hint"

    if mode == "expect_blocked":
        if "Modification requests are not permitted" in s:
            return "pass", ""
        return "fail", "expected MODIFY_BLOCKED_MSG banner"

    if mode == "expect_warn_no_qmgr":
        if "without `qmgr_name`" in s:
            return "pass", ""
        return "fail", "expected '⚠️ ... without `qmgr_name`' warning"

    if mode == "expect_error_envelope":
        # Three valid shapes for a sanitised error:
        #   1. Top-level {"status": "error", ...}
        #   2. Text starting with ❌/🚫/⚠️
        #   3. JSON envelope whose dict has any key ending in "_error"
        #      (e.g. ace_server_explore's {"applications_error": "...", ...})
        has_field_error = False
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    has_field_error = any(
                        k.endswith("_error") for k in parsed.keys()
                    )
            except Exception:
                pass
        if parsed_status == "error" or is_err or is_warn or has_field_error:
            return "pass", ""
        return "fail", "expected sanitised error envelope"

    if mode == "offline":
        if is_warn or is_err or parsed_status == "error":
            return "fail", "offline tool returned an error envelope"
        return "pass", ""
    if is_warn:
        return "skip", "upstream curated ⚠️ envelope"
    if parsed_status == "error":
        return "skip", "upstream JSON status=error"
    if is_err:
        return "skip", "manifest miss / restricted"
    return "pass", ""


async def main():
    # Tool outputs contain emoji (🔍 ❌ ⚠️). Windows defaults to cp1252 which
    # cannot encode them, so reconfigure stdout to UTF-8 before any print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError:
        print("FAIL: mcp SDK not installed in this venv")
        return 1

    auth = None
    if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
        auth = httpx.BasicAuth(MCP_AUTH_USER, MCP_AUTH_PASSWORD)
        print(f"Basic Auth user={MCP_AUTH_USER}")

    heading(f"mqacemcpserver-single AWS smoke ({SSE_URL})")

    parsed = urlparse(SSE_URL)
    use_tls = parsed.scheme == "https"
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if use_tls else 80)
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            r, w = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx), timeout=5.0)
        else:
            r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        w.close()
        await w.wait_closed()
        print(f"  TLS/TCP handshake OK -> {host}:{port}")
    except Exception as e:
        print(f"  FAIL handshake: {type(e).__name__}: {e}")
        return 1

    async with sse_client(SSE_URL, auth=auth, httpx_client_factory=_make_insecure_httpx_client) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            print("  MCP session initialised")

            tools_result = await session.list_tools()
            names = {t.name for t in tools_result.tools}
            print(f"\n[Tool catalogue: {len(names)}]")
            for t in tools_result.tools:
                desc = (t.description or "").strip().split("\n")[0]
                if len(desc) > 70:
                    desc = desc[:70] + "..."
                print(f"  - {t.name}: {desc}")

            missing = EXPECTED_TOOLS - names
            extra = names - EXPECTED_TOOLS
            if missing:
                print(f"  FAIL: missing tools: {sorted(missing)}")
                return 1
            if extra:
                print(f"  FAIL: unexpected tools: {sorted(extra)}")
                return 1
            print("  OK: catalogue == 7 expected tools")

            selectors = [a for a in sys.argv[1:] if not a.startswith("-")]
            flags = [a for a in sys.argv[1:] if a.startswith("-")]
            # Preview verbosity: default 12 lines; --full shows everything,
            # --lines=N shows N lines.
            preview_limit = 12
            for f in flags:
                if f in ("--full", "-f"):
                    preview_limit = None
                elif f.startswith("--lines="):
                    try:
                        preview_limit = int(f.split("=", 1)[1])
                    except ValueError:
                        pass
            calls = select_calls(CALLS, selectors)
            if selectors:
                print(f"\n[Filter: {selectors} -> {len(calls)}/{len(CALLS)} calls]")
                if not calls:
                    print(f"  No calls match {selectors}. "
                          f"Use a category (mq/ace/cert) or a tool name.")
                    return 1

            results = []
            for i, (name, args, mode) in enumerate(calls, start=1):
                heading(f"[{i}] {name}  ({mode})  args={json.dumps(args)}")
                try:
                    res = await session.call_tool(name, args)
                    text = res.content[0].text if res.content and getattr(res.content[0], "text", None) else ""
                    preview(text, preview_limit)
                    outcome, reason = classify(text, mode)
                    results.append((i, name, mode, outcome, reason))
                    print(f"  -> {outcome}{(' (' + reason + ')') if reason else ''}")
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    print(f"  RAISED: {msg}")
                    results.append((i, name, mode, "fail", msg))

            passed = sum(1 for *_, o, _ in results if o == "pass")
            skipped = sum(1 for *_, o, _ in results if o == "skip")
            failed = sum(1 for *_, o, _ in results if o == "fail")
            heading(f"Summary: pass={passed} skip={skipped} fail={failed} of {len(results)}")

            # Column-aligned summary: index, tool, online/offline kind, result, mode tag, reason.
            print(f"  {'#':>3}  {'Tool':<22} {'Kind':<8} {'Result':<6}  {'Mode':<22} Reason")
            print(f"  {'-'*3}  {'-'*22} {'-'*8} {'-'*6}  {'-'*22} ------")
            for idx, n, m, o, r in results:
                kind = "online" if m == "live" else "offline"
                reason_col = r if r else ""
                print(f"  {idx:>3}  {n:<22} {kind:<8} {o:<6}  {m:<22} {reason_col}")
            return 0 if failed == 0 else 1


if __name__ == "__main__":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sys.exit(asyncio.run(main()))
