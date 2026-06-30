"""SSE smoke-test client for mqacemcpserver.

Connects to the composites server, lists tools, then exercises each of the
composite tools. For the Streamable HTTP transport (`/mcp`) use
`smoke_test_http.py` instead.

Each call also prints the backend MQ/ACE endpoint(s) the server hit, read back
from its JSONL query log (same-host only). Pass --no-endpoints to suppress, or
set MCP_QUERY_LOG_DIR if the server's logs/ live elsewhere.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import ssl
import sys
import time
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
MCP_PORT = os.getenv("MCP_PORT", "8010")
MCP_TLS_CERT = os.getenv("MCP_TLS_CERT", "")
MCP_TLS_KEY = os.getenv("MCP_TLS_KEY", "")

if MCP_HOST in ("", "0.0.0.0"):
    MCP_HOST = "127.0.0.1"

_scheme = "https" if (MCP_TLS_CERT and MCP_TLS_KEY) else "http"
SSE_URL = os.getenv("MCP_REMOTE_SERVER_URL", f"{_scheme}://{MCP_HOST}:{MCP_PORT}/sse")

# The server records the backend endpoint(s) it hit for each call in its JSONL
# query log. When the client runs on the same host as the server we can read
# that log back and show, per call, exactly which MQ/ACE URL(s) were called.
# Defaults to this build's logs/; override with MCP_QUERY_LOG_DIR.
QUERY_LOG_DIR = os.getenv("MCP_QUERY_LOG_DIR", str(PROJECT_ROOT / "logs"))


def _newest_query_log():
    """Path to the most-recently-modified queries-*.jsonl, or None."""
    files = glob.glob(os.path.join(QUERY_LOG_DIR, "queries-*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def _last_record(path):
    """Parse and return the last non-empty JSON object in `path`, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if line:
            try:
                return json.loads(line)
            except Exception:
                return None
    return None


def _read_call_endpoints(seen_ids, retries=8, delay=0.05):
    """Best-effort: after a tool call, read the server's query log for the
    endpoints it just recorded. Correlates by request_id (each call appends one
    line). Returns (endpoints_list, found_bool). `seen_ids` must be pre-seeded
    with the log's last request_id BEFORE the run so we never grab a stale line.
    """
    for _ in range(retries):
        path = _newest_query_log()
        if path:
            rec = _last_record(path)
            if rec:
                rid = rec.get("request_id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    return rec.get("endpoints") or [], True
        time.sleep(delay)
    return [], False


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

# Object names below are drawn from the current offline manifests under
# resources/ (qmgr_dump.csv, node_dump.csv, node_config.csv, cert_dump.csv).
# QMs:    MQNODE1, MQNODE2, MQQM1, MQREPO1, MQREPO2  (all on localhost)
# Queues: QL.INPUT / QL.OUT / QL.SOURCE (MQNODE1/2), DEV.QUEUE.1 (MQQM1),
#         QL.ADMIN.REQUEST(+.ALIAS) (MQREPO1), QL.REPO.AUDIT (MQREPO2)
# Chans:  <QM>.CLUSRCVR / <QM>.CLUSSDR, DEV.APP.SVRCONN (MQQM1)
# ACE:    NODE1, NODE2 -> servers ACE_DEMO_CACHE/CONNECTORS/MESSAGING/TRANSFORM
# Certs:  aliases mq-ssl-2026, mqweb-https, ace-admin-tls, ace-rest-api-tls, ...
CALLS = [
    # --- mq_queue_inspect (6) -------------------------------------------------
    ("mq_queue_inspect", {"queue_names": ["QL.INPUT"]}, "live"),                                          # default MQ_URL_BASE QM
    ("mq_queue_inspect", {"queue_names": ["QL.INPUT", "QL.OUT"], "qmgr_name": "MQNODE1"}, "live"),        # MULTI-TARGET: two queues, one call
    ("mq_queue_inspect", {"queue_names": ["QL.SOURCE"], "qmgr_name": "MQNODE1"}, "live"),
    ("mq_queue_inspect", {"queue_names": ["DEV.QUEUE.1"], "qmgr_name": "MQQM1"}, "live"),
    ("mq_queue_inspect", {"queue_names": ["QL.ADMIN.REQUEST.ALIAS"], "qmgr_name": "MQREPO1"}, "live"),    # QALIAS -> TARGET resolution
    ("mq_queue_inspect", {"queue_names": ["NOPE.DOES.NOT.EXIST"], "qmgr_name": "MQNODE1"}, "expect_not_found"),

    # --- mq_channel_inspect (4) -----------------------------------------------
    ("mq_channel_inspect", {"channel_names": ["MQNODE1.CLUSRCVR"], "qmgr_name": "MQNODE1"}, "live"),
    ("mq_channel_inspect", {"channel_names": ["MQNODE1.CLUSRCVR", "MQNODE1.CLUSSDR"], "qmgr_name": "MQNODE1"}, "live"),  # MULTI-TARGET: two channels, one call
    ("mq_channel_inspect", {"channel_names": ["DEV.APP.SVRCONN"], "qmgr_name": "MQQM1"}, "live"),         # SVRCONN channel
    ("mq_channel_inspect", {"channel_names": ["CH.UNKNOWN.XYZ"], "qmgr_name": "MQNODE1"}, "expect_not_found"),

    # --- mq_host_overview (13) ------------------------------------------------
    ("mq_host_overview", {}, "live"),                                                              # default MQ_URL_BASE
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"]}, "live"),                                     # resolved via manifest
    ("mq_host_overview", {"qmgr_names": ["MQNODE1", "MQREPO1"]}, "live"),                          # MULTI-TARGET: two QMs, one call
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QMGR ALL"}, "live"),    # + read-only DISPLAY
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.INPUT) ALL"}, "live"),                                       # full queue properties
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.INPUT) MAXDEPTH CURDEPTH QDEPTHHI QDEPTHLO"}, "live"),       # max depth + thresholds
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.INPUT) CRDATE CRTIME"}, "live"),                             # queue creation date/time
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QMGR DEADQ DEFXMITQ MAXMSGL MAXHANDS CCSID"}, "live"),                 # focused QMGR properties
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QLOCAL(QL.*) CURDEPTH MAXDEPTH"}, "live"),                             # wildcard queue scan
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY CHANNEL(MQNODE1.CLUSRCVR) ALL"}, "live"),                              # channel properties
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY CHSTATUS(MQNODE1.CLUSRCVR)"}, "live"),                                 # channel status
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DEFINE QLOCAL(SMOKE.BLOCK.TEST)"}, "expect_blocked"),
    ("mq_host_overview", {"hostnames": ["loq-mq01"], "mqsc_command": "DISPLAY QMGR"}, "expect_warn_no_qmgr"),

    # --- ace_node_overview (5) ------------------------------------------------
    ("ace_node_overview", {"nodes": ["NODE1"]}, "live"),                               # configured node (resources/node_config.csv)
    ("ace_node_overview", {"nodes": ["NODE1", "NODE2"]}, "live"),                      # MULTI-TARGET: two nodes, one call
    ("ace_node_overview", {"nodes": ["NODE2"]}, "live"),
    ("ace_node_overview", {"nodes": ["NODE3"]}, "expect_error_envelope"),              # not configured
    ("ace_node_overview", {"nodes": ["GHOST.NODE"]}, "expect_error_envelope"),

    # --- ace_server_explore (6) -----------------------------------------------
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CACHE"]}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CACHE", "ACE_DEMO_TRANSFORM"]}, "live"),  # MULTI-TARGET: two servers, one call
    ("ace_server_explore", {"node": "NODE2", "servers": ["ACE_DEMO_MESSAGING"]}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CONNECTORS"]}, "live"),
    ("ace_server_explore", {"node": "NODE1", "servers": ["ACE_DEMO_CONNECTORS"], "application": "AmazonS3"}, "live"),  # scope flows to one application
    ("ace_server_explore", {"node": "NODE1", "servers": ["GHOST.SERVER"]}, "expect_error_envelope"),

    # --- ace_search (5) -------------------------------------------------------
    ("ace_search", {"search_strings": [""], "scope": "nodes"}, "offline"),
    ("ace_search", {"search_strings": ["ACE_DEMO_TRANSFORM"], "scope": "dump"}, "offline"),
    ("ace_search", {"search_strings": ["AmazonS3", "Salesforce"], "scope": "dump"}, "offline"),  # MULTI-TARGET: match either, one call
    ("ace_search", {"search_strings": [""]}, "offline"),                                         # default scope = all
    ("ace_search", {"search_strings": ["x"], "scope": "bogus"}, "expect_error_envelope"),

    # --- get_cert_details (4) -------------------------------------------------
    ("get_cert_details", {"search_strings": ["mq-ssl-2026"]}, "offline"),                            # match by alias
    ("get_cert_details", {"search_strings": ["mqweb-https"]}, "offline"),                            # match by alias
    ("get_cert_details", {"search_strings": ["ace-admin-tls", "ace-rest-api-tls"]}, "offline"),      # MULTI-TARGET: two queries merged, one call
    ("get_cert_details", {"search_strings": ["no-such-cert-anywhere"]}, "offline"),                  # success, empty results
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
        # Two valid shapes depending on reachability:
        #   1. The sanitised "❌ ... not found ..." hint (QM not in manifest, or
        #      the host could not be queried).
        #   2. The live MQSC object-not-found text returned when the QM IS
        #      reachable but the object is absent, e.g.
        #      "AMQ8147E: IBM MQ object ... not found." / "AMQ8420I: ... not found.".
        if "not found" in s.lower():
            return "pass", ""
        return "fail", "expected a 'not found' signal (❌ hint or AMQ…not found)"

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

    heading(f"mqacemcpserver smoke ({SSE_URL})")

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
            # Per-call backend endpoint display (read back from the server's
            # query log) is on by default; pass --no-endpoints to suppress it.
            show_endpoints = "--no-endpoints" not in flags
            for f in flags:
                if f in ("--full", "-f"):
                    preview_limit = None
                elif f.startswith("--lines="):
                    try:
                        preview_limit = int(f.split("=", 1)[1])
                    except ValueError:
                        pass

            # Seed the seen-ids set with the log's current last request_id so the
            # first call doesn't pick up a stale line written before this run.
            seen_ids = set()
            if show_endpoints:
                _p = _newest_query_log()
                _last = _last_record(_p) if _p else None
                if _last and _last.get("request_id"):
                    seen_ids.add(_last["request_id"])
                elif _p is None:
                    print(f"  (endpoint display: no query log under {QUERY_LOG_DIR}; "
                          f"set MCP_QUERY_LOG_DIR or use --no-endpoints)")
                    show_endpoints = False

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
                    if show_endpoints:
                        eps, found = _read_call_endpoints(seen_ids)
                        if not found:
                            print("  ↳ endpoints: (not recorded yet / log unavailable)")
                        elif eps:
                            print("  ↳ endpoints:")
                            for ep in eps:
                                print(f"      {ep}")
                        else:
                            print("  ↳ endpoints: (none — offline CSV / no HTTP call)")
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
