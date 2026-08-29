"""Streamable-HTTP smoke-test client for mqacemcpserver.

Connects to the composites server over the **Streamable HTTP** transport
(`/mcp` endpoint), lists tools, then exercises each of the composite tools.
Use this when the server runs with `MCP_TRANSPORT=streamable-http` (the
default). For the legacy SSE transport (`/sse`) use `smoke_test.py` instead.

This client is fully self-contained — it does NOT import `smoke_test.py`. The
two clients are kept independent on purpose; if you change the test data
(`CALLS`) or the result rules (`classify`) here, mirror the change in
`smoke_test.py` if you want both transports tested the same way.

WHICH SERVER IT TALKS TO: the three constants MCP_ENDPOINT_URL, MCP_USER and
MCP_PASSWORD below. Nothing is read from the environment or from a .env file —
edit those three and run. For a one-off run against a different server, pass
--url / --user / --password, or -i to be prompted.

Usage (from the build folder, using this build's venv):
    .venv\\Scripts\\python.exe clients\\smoke_test_http.py            # all calls
    .venv\\Scripts\\python.exe clients\\smoke_test_http.py mq         # filter by category
    .venv\\Scripts\\python.exe clients\\smoke_test_http.py --full     # full output previews

    python clients\\smoke_test_http.py --url https://host:8010/mcp --user u --password p
    python clients\\smoke_test_http.py -i     # prompt for endpoint/user/password

Each call also prints the backend MQ/ACE endpoint(s) the server hit, read back
from its JSONL query log (same-host only). Pass --no-endpoints to suppress, or
edit QUERY_LOG_DIR if the server's logs/ live elsewhere.
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
import time
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse

import httpx
import urllib3

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ===========================================================================
#  EDIT THESE THREE — the server this client talks to.
#  Nothing is read from the environment or from .env; what is written here is
#  what runs. Override for a single run with --url / --user / --password, or
#  pass -i to be prompted (these values are then offered as the defaults).
# ===========================================================================
MCP_ENDPOINT_URL = "https://localhost:8010/mcp"
MCP_USER = "mcpadmin"
MCP_PASSWORD = ""          # blank on purpose - fill in, or pass --password / -i
# ===========================================================================

# The server records the backend endpoint(s) it hit for each call in its JSONL
# query log. When the client runs on the same host as the server we can read
# that log back and show, per call, exactly which MQ/ACE URL(s) were called.
# Point this at the server's logs/ if they do not live beside this build.
QUERY_LOG_DIR = str(PROJECT_ROOT / "logs")


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


def normalise_url(raw):
    """Accept the shorthand people actually type and return a full /mcp URL.

    "host:8010" -> "https://host:8010/mcp";  "https://host:8010" -> ".../mcp".
    A scheme-less value assumes https because the server ships with TLS
    configured; give an explicit http:// URL for a plaintext endpoint. An
    existing path (e.g. a proxy route) is left alone.
    """
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.path in ("", "/"):
        url = f"{url.rstrip('/')}/mcp"
    return url


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="smoke_test_http.py",
        description=(
            "Streamable-HTTP smoke test for mqacemcpserver. With no options it "
            "uses the MCP_ENDPOINT_URL / MCP_USER / MCP_PASSWORD constants set "
            "at the top of this file; --url/--user/--password override them for "
            "one run, and -i prompts for them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --url https://host:8010/mcp --user mcpadmin --password s3cr3t\n"
            "  %(prog)s -i                      # prompt for endpoint + credentials\n"
            "  %(prog)s --url host:8010 mq      # shorthand URL, MQ tools only\n"
        ),
    )
    parser.add_argument(
        "selectors",
        nargs="*",
        metavar="SELECTOR",
        help="filter calls by category (mq/ace/cert) or tool name; default runs all",
    )
    parser.add_argument(
        "--url", "--endpoint", dest="url", metavar="URL",
        help="MCP endpoint, e.g. https://host:8010/mcp (a bare host:port is completed for you)",
    )
    parser.add_argument("--user", "-u", metavar="NAME", help="Basic Auth username")
    parser.add_argument("--password", "-p", metavar="PASS", help="Basic Auth password")
    parser.add_argument(
        "-i", "--ask", action="store_true",
        help="prompt for endpoint, user and password (the constants above are offered as defaults)",
    )
    parser.add_argument(
        "--no-auth", action="store_true",
        help="connect anonymously, ignoring MCP_USER / MCP_PASSWORD",
    )
    parser.add_argument(
        "--full", "-f", action="store_true", help="print full output previews",
    )
    parser.add_argument(
        "--lines", type=int, metavar="N", default=12,
        help="preview N lines per call (default: 12)",
    )
    parser.add_argument(
        "--no-endpoints", dest="show_endpoints", action="store_false",
        help="do not read back backend endpoints from the server's query log",
    )
    return parser.parse_args(argv)


def _ask(label, default=""):
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"  {label}{shown}: ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_secret(label="Password"):
    """Read a password without echoing it — when there is a terminal to read.

    On Windows getpass() reads the console directly, so it would block forever
    when stdin is a pipe (CI, `echo ... | script`). Fall back to a plain read
    in that case; there is no echo to suppress anyway.
    """
    if sys.stdin.isatty():
        try:
            return getpass(f"  {label}: ")
        except (EOFError, OSError):
            return ""
    try:
        return input(f"  {label}: ").strip()
    except EOFError:
        return ""


def resolve_target(args):
    """Work out (url, user, password) from flags, prompts, then the constants.

    Returns the URL empty only when there is nothing to fall back on and we
    cannot prompt; the caller turns that into a clean error.
    """
    url, user, password = args.url, args.user, args.password

    if args.ask:
        # Prompt for whatever the flags did not already pin down, offering the
        # constant as the default so Enter just uses what the file says.
        print("Target MCP server (press Enter to accept the default):")
        url = url or _ask("Endpoint", MCP_ENDPOINT_URL)
        if not args.no_auth:
            user = user or _ask("User", MCP_USER)
            if user and password is None:
                # Not echoed, so it is never left in shell history.
                password = _ask_secret() or MCP_PASSWORD

    url = normalise_url(url or MCP_ENDPOINT_URL)

    if args.no_auth:
        return url, "", ""

    user = user if user is not None else MCP_USER
    if password is None:
        # A user supplied on the command line without a password: ask rather
        # than silently pairing it with MCP_PASSWORD.
        password = _ask_secret() if args.user else MCP_PASSWORD
    return url, user, password


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
    "mq_queue_inspect", "mq_channel_inspect", "mq_connection_verify", "mq_host_overview",
    "ace_node_overview", "ace_connection_verify", "ace_server_explore", "ace_search",
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

    # --- mq_host_overview (18) ------------------------------------------------
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
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY QMSTATUS ALL"}, "live"),                                                # QM run-state / restart time (STATUS, STARTDA+STARTTI)
    ("mq_host_overview", {"qmgr_names": ["MQNODE1", "MQREPO1"], "mqsc_command": "DISPLAY QMSTATUS ALL"}, "live"),                                     # MULTI-TARGET: QMSTATUS per QM in one call
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY LSSTATUS(*) ALL"}, "live"),                                             # listeners
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY TOPIC(*) TOPICSTR DESCR DEFPRTY"}, "live"),                             # topics
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DISPLAY SUB(*) SUBID DEST TOPICSTR"}, "live"),                                  # subscriptions
    ("mq_host_overview", {"qmgr_names": ["MQNODE1"], "mqsc_command": "DEFINE QLOCAL(SMOKE.BLOCK.TEST)"}, "expect_blocked"),
    ("mq_host_overview", {"hostnames": ["loq-mq01"], "mqsc_command": "DISPLAY QMGR"}, "expect_warn_no_qmgr"),

    # --- mq_connection_verify (5) ---------------------------------------------
    # OFFLINE fact-check of a pasted MQ connection error. Values come from
    # qmgr_dump.csv: MQREPO1 listens on 1414 and MQREPO1.CLUSRCVR has
    # CONNAME('server1(1414)'); MQNODE1 listens on 1420.
    ("mq_connection_verify", {"qmgr_name": "MQREPO1", "hostname": "server1", "port": 1414,
                              "channel": "MQREPO1.CLUSRCVR"}, "expect_verify_ok"),                     # every field correct — the AMQ9213 example
    ("mq_connection_verify", {"qmgr_name": "MQREPO1", "port": 1420}, "expect_verify_mismatch"),        # 1420 is MQNODE1's port, MQREPO1 listens on 1414
    ("mq_connection_verify", {"qmgr_name": "MQREPO1", "hostname": "wrong-host", "port": 1414,
                              "channel": "MQREPO1.CLUSRCVR"}, "expect_verify_mismatch"),               # partial: port/channel right, host wrong -> ⚠️
    ("mq_connection_verify", {"qmgr_name": "MQNODE1", "channel": "CH.NOT.DEFINED"}, "expect_verify_mismatch"),  # channel not defined on that QM
    ("mq_connection_verify", {"qmgr_name": "GHOST.QM"}, "expect_verify_mismatch"),                     # QM absent from the manifest — reported, not an error

    # --- ace_node_overview (5) ------------------------------------------------
    ("ace_node_overview", {"nodes": ["NODE1"]}, "live"),                               # configured node (resources/node_config.csv)
    ("ace_node_overview", {"nodes": ["NODE1", "NODE2"]}, "live"),                      # MULTI-TARGET: two nodes, one call
    ("ace_node_overview", {"nodes": ["NODE2"]}, "live"),
    ("ace_node_overview", {"nodes": ["NODE3"]}, "expect_error_envelope"),              # not configured
    ("ace_node_overview", {"nodes": ["GHOST.NODE"]}, "expect_error_envelope"),

    # --- ace_connection_verify (4) --------------------------------------------
    # OFFLINE fact-check against node_config.csv: NODE1 -> localhost:4414,
    # NODE2 -> localhost:4415.
    ("ace_connection_verify", {"node": "NODE1", "host": "localhost", "port": 4414}, "expect_verify_ok"),        # every field correct
    ("ace_connection_verify", {"node": "NODE1", "host": "localhost", "port": 4499}, "expect_verify_mismatch"),   # wrong port — the BIP1809 example
    ("ace_connection_verify", {"node": "NODE2"}, "offline"),                                                     # node only — nothing to compare, reports host:port
    ("ace_connection_verify", {"node": "NODE3"}, "expect_verify_mismatch"),                                      # not in node_config.csv — reported, not an error

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
    # A FastMCP framework error (bad/missing arguments, or the tool itself
    # raised) is a smoke-test bug, not a valid tool response — fail it in EVERY
    # mode so it can never hide behind a "pass". Classic case: passing
    # `search_string` instead of the required `search_strings` list.
    if s.startswith("Error executing tool") or "validation error for" in s:
        return "fail", "tool/argument error (check this call's args)"
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

    # The two fact-check tools (mq_connection_verify / ace_connection_verify)
    # always SUCCEED — the verdict lives in their "Overall:" line. A wrong
    # verdict is a real regression, so assert the verdict itself rather than
    # merely "no error envelope".
    if mode == "expect_verify_ok":
        if "Overall: ✅" in s:
            return "pass", ""
        return "fail", "expected 'Overall: ✅ all supplied details check out.'"

    if mode == "expect_verify_mismatch":
        # ❌ = nothing supplied matched (or the QM/node itself is unknown);
        # ⚠️ = some fields matched and some did not. Both are negative verdicts.
        if "Overall: ❌" in s or "Overall: ⚠️" in s:
            return "pass", ""
        return "fail", "expected a negative 'Overall: ❌/⚠️' verdict"

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


def _root_cause(err):
    """Innermost exception of an anyio ExceptionGroup, else err itself.

    The MCP client runs its transport in a task group, so a 401 or a refused
    connection arrives wrapped in an ExceptionGroup whose traceback buries the
    one line that matters.
    """
    for _ in range(5):
        nested = getattr(err, "exceptions", None)
        if not nested:
            break
        err = nested[0]
    return err


def _explain_connect_failure(err, url, user):
    """Print a one-line diagnosis for a failure to reach/authenticate."""
    cause = _root_cause(err)
    status = getattr(getattr(cause, "response", None), "status_code", None)

    if status in (401, 403):
        who = f"user={user}" if user else "no credentials sent"
        print(f"FAIL: {url} rejected the credentials ({status}, {who}).")
        print("      Check --user/--password, or use --no-auth if the server is open.")
    elif isinstance(cause, httpx.ConnectError):
        print(f"FAIL: could not connect to {url} ({type(cause).__name__}).")
        print("      Check the host/port, that the server is running, and http:// vs https://.")
    elif isinstance(cause, (httpx.ReadTimeout, httpx.ConnectTimeout)):
        print(f"FAIL: timed out talking to {url}.")
    elif isinstance(cause, httpx.RemoteProtocolError) and url.startswith("http://"):
        # Classic symptom of speaking plaintext to a TLS listener.
        print(f"FAIL: {url} closed the connection without responding.")
        print("      That port looks like it wants TLS — try https:// instead.")
    elif status is not None:
        print(f"FAIL: {url} returned HTTP {status}.")
        print("      If this is not the MCP endpoint, include the right path (e.g. /mcp).")
    else:
        print(f"FAIL: could not start an MCP session with {url}")
        print(f"      {type(cause).__name__}: {cause}")


async def main(opts):
    # Tool outputs contain emoji (🔍 ❌ ⚠️). Windows defaults to cp1252 which
    # cannot encode them, so reconfigure stdout to UTF-8 before any print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        from mcp import ClientSession  # noqa: F401
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401
    except ImportError:
        print("FAIL: mcp SDK not installed in this venv")
        return 1

    url, user, password = resolve_target(opts)
    if not url:
        print("FAIL: no MCP endpoint. Pass --url https://host:8010/mcp, use -i, "
              "or set MCP_ENDPOINT_URL at the top of this file.")
        return 1

    auth = None
    if user and password:
        auth = httpx.BasicAuth(user, password)
        print(f"Basic Auth user={user}")
    elif user:
        print(f"WARN: user={user} given with no password — connecting anonymously.")

    heading(f"mqacemcpserver smoke ({url})")

    try:
        return await _smoke(opts, url, auth)
    except Exception as err:  # noqa: BLE001
        # Connection/auth problems surface here; per-call failures are caught
        # inside the loop and reported as test results instead.
        _explain_connect_failure(err, url, user)
        return 1


async def _smoke(opts, url, auth):
    """Open the MCP session and run the selected calls. Returns an exit code."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(
        url, auth=auth, httpx_client_factory=_make_insecure_httpx_client
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
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
            print(f"  OK: catalogue == {len(EXPECTED_TOOLS)} expected tools")

            selectors = opts.selectors
            # Preview verbosity: default 12 lines; --full shows everything,
            # --lines N shows N lines.
            preview_limit = None if opts.full else opts.lines
            # Per-call backend endpoint display (read back from the server's
            # query log) is on by default; pass --no-endpoints to suppress it.
            show_endpoints = opts.show_endpoints

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
    try:
        sys.exit(asyncio.run(main(parse_args())))
    except KeyboardInterrupt:
        # Ctrl-C at an interactive prompt should not dump a traceback.
        print("\nAborted.")
        sys.exit(130)
