"""
SSE smoke-test client for the unified MQ + ACE MCP server (mqacemcpserver).

Modelled on mqmcp/clients/test_https_client.py — same connection pattern,
extended to call every tool the server registers (7 MQ + 6 ACE = 13 total).

Usage
-----
    python clients/test_https_client.py            # full smoke test
    python clients/test_https_client.py --list     # just list tools and exit
    python clients/test_https_client.py --only find_mq_object   # single tool

Run the server first (in another shell), with SSE transport:
    $env:MCP_TRANSPORT="sse"
    .venv\\Scripts\\python.exe mqacemcpserver.py

The client picks up connection settings from the project .env:
    MCP_REMOTE_SERVER_URL   full URL override (takes precedence)
    MCP_HOST, MCP_PORT      used to derive the URL when override is unset
    MCP_TLS_CERT/KEY        if both set, default to https://...
    MCP_AUTH_USER/PASSWORD  Basic Auth on the SSE endpoint

Exit codes: 0 = no failures (live tools may "skip" if backends unreachable),
            1 = at least one tool raised or returned an unexpected error.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Env
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

MCP_AUTH_USER = os.getenv("MCP_AUTH_USER", "")
MCP_AUTH_PASSWORD = os.getenv("MCP_AUTH_PASSWORD", "")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = os.getenv("MCP_PORT", "8000")
MCP_TLS_CERT = os.getenv("MCP_TLS_CERT", "")
MCP_TLS_KEY = os.getenv("MCP_TLS_KEY", "")

if MCP_HOST in ("", "0.0.0.0"):
    MCP_HOST = "127.0.0.1"

_default_scheme = "https" if (MCP_TLS_CERT and MCP_TLS_KEY) else "http"
SSE_URL = os.getenv(
    "MCP_REMOTE_SERVER_URL", f"{_default_scheme}://{MCP_HOST}:{MCP_PORT}/sse"
)

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
GREY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def heading(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 64}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 64}{RESET}")


def success(text: str) -> None:
    print(f"  {GREEN}✔ {text}{RESET}")


def warn(text: str) -> None:
    print(f"  {YELLOW}⚠ {text}{RESET}")


def fail(text: str) -> None:
    print(f"  {RED}✖ {text}{RESET}")


def info(text: str) -> None:
    print(f"  {CYAN}ℹ {text}{RESET}")


def dim(text: str) -> None:
    print(f"  {GREY}{text}{RESET}")


# ---------------------------------------------------------------------------
# httpx factory that skips TLS verification (self-signed certs)
# ---------------------------------------------------------------------------
def _make_insecure_httpx_client(
    headers: dict[str, str] | None = None,
    timeout=None,
    auth=None,
):
    import httpx

    kwargs: dict = {"follow_redirects": True, "verify": False}
    kwargs["timeout"] = timeout if timeout is not None else httpx.Timeout(30.0, read=300.0)
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


# ---------------------------------------------------------------------------
# Sample inputs — derived from resources/qmgr_dump.csv, resources/node_config.csv,
# and the historical query log. Edit this block to adapt to a different env.
# ---------------------------------------------------------------------------
SAMPLE_QM = "MQQMGR2"
SAMPLE_QUEUE = "QL.IN.APP1"
SAMPLE_SEARCH_MQ = "QL.IN"
SAMPLE_ACE_NODE = "NODE1"
SAMPLE_ACE_NODE_LIVE = "NODE2"   # NODE2 has IS001+snaplogic1 in seed data
SAMPLE_ACE_SERVER = "IS001"
SAMPLE_ACE_APP = "snaplogic1"
SAMPLE_BIP_TOKEN = "BIP"


def discover_channel_name() -> str | None:
    """Read the first CHANNEL row from the offline manifest. Best-effort only."""
    csv_path = PROJECT_ROOT / "resources" / "qmgr_dump.csv"
    if not csv_path.exists():
        return None
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            next(f, None)  # header
            for line in f:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 5 and parts[3].upper() == "CHANNEL":
                    objectdef = parts[4]
                    if "CHANNEL(" in objectdef.upper():
                        start = objectdef.upper().index("CHANNEL(") + len("CHANNEL(")
                        end = objectdef.index(")", start)
                        name = objectdef[start:end].strip().strip("'")
                        if name:
                            return name
    except Exception:
        return None
    return None


def build_tool_table(channel_name: str | None) -> list[dict]:
    """Return the table of tool calls to execute.

    Each entry: {name, args, mode} where mode is 'offline' or 'live'.
    """
    table: list[dict] = [
        {"name": "find_mq_object",
         "args": {"search_string": SAMPLE_SEARCH_MQ, "object_type": "QLOCAL"},
         "mode": "offline"},
        {"name": "dspmq",
         "args": {"qmgr_name": SAMPLE_QM},
         "mode": "live"},
        {"name": "dspmqver",
         "args": {"qmgr_name": SAMPLE_QM},
         "mode": "live"},
        {"name": "runmqsc",
         "args": {"qmgr_name": SAMPLE_QM, "mqsc_command": "DISPLAY QMGR ALL"},
         "mode": "live"},
        {"name": "run_mqsc_for_object",
         "args": {"object_name": SAMPLE_QUEUE,
                  "mqsc_command": f"DISPLAY QLOCAL({SAMPLE_QUEUE}) CURDEPTH"},
         "mode": "live"},
        {"name": "get_queue_depth",
         "args": {"queue_name": SAMPLE_QUEUE},
         "mode": "live"},
        {"name": "get_channel_status",
         "args": {"channel_name": channel_name or "SYSTEM.DEF.SVRCONN"},
         "mode": "live"},
        {"name": "list_ace_nodes",
         "args": {},
         "mode": "offline"},
        {"name": "get_ace_node_status",
         "args": {"node": SAMPLE_ACE_NODE},
         "mode": "live"},
        {"name": "list_ace_servers",
         "args": {"node": SAMPLE_ACE_NODE},
         "mode": "live"},
        {"name": "list_ace_applications",
         "args": {"node": SAMPLE_ACE_NODE_LIVE, "server": SAMPLE_ACE_SERVER},
         "mode": "live"},
        {"name": "list_ace_message_flows",
         "args": {"node": SAMPLE_ACE_NODE_LIVE,
                  "server": SAMPLE_ACE_SERVER,
                  "app": SAMPLE_ACE_APP},
         "mode": "live"},
        {"name": "search_ace_local_dump",
         "args": {"search_string": SAMPLE_BIP_TOKEN},
         "mode": "offline"},
    ]
    return table


# ---------------------------------------------------------------------------
# Outcome interpretation
# ---------------------------------------------------------------------------
def classify_output(text: str, mode: str) -> tuple[str, str]:
    """Return (outcome, reason). outcome ∈ {'pass','skip','fail'}.

    - MQ curated errors start with ❌ or 🚫 (manifest/access) or ⚠️ (upstream).
    - ACE tools return JSON; status=='error' means the upstream call failed
      gracefully and was sanitised.
    - Offline tools that emit the same envelopes are real failures (seed data
      is supposed to satisfy them).
    """
    stripped = text.lstrip()
    is_warning_envelope = stripped.startswith("⚠️") or stripped.startswith("⚠")
    is_error_envelope = stripped.startswith("❌") or stripped.startswith("🚫")

    parsed_status = None
    if stripped.startswith("{"):
        try:
            parsed_status = json.loads(stripped).get("status")
        except (json.JSONDecodeError, AttributeError):
            parsed_status = None

    if mode == "offline":
        if is_warning_envelope or is_error_envelope or parsed_status == "error":
            return "fail", "offline tool returned an error envelope"
        return "pass", ""

    # live tools
    if is_warning_envelope:
        return "skip", "upstream unreachable (curated ⚠️ envelope)"
    if parsed_status == "error":
        return "skip", "upstream unreachable (JSON status=error)"
    if is_error_envelope:
        return "skip", "object not in manifest (❌/🚫)"
    return "pass", ""


def preview(text: str, limit: int = 10) -> None:
    lines = text.split("\n")
    for line in lines[:limit]:
        print(f"    {GREY}{line}{RESET}")
    if len(lines) > limit:
        print(f"    {GREY}… ({len(lines) - limit} more lines){RESET}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
EXPECTED_TOOLS = {
    # MQ
    "find_mq_object", "dspmq", "dspmqver", "runmqsc", "run_mqsc_for_object",
    "get_queue_depth", "get_channel_status",
    # ACE
    "list_ace_nodes", "get_ace_node_status", "list_ace_servers",
    "list_ace_applications", "list_ace_message_flows", "search_ace_local_dump",
}


async def run(only: str | None = None, list_only: bool = False) -> int:
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError:
        fail("MCP SDK not installed. Run: pip install mcp")
        return 1

    import httpx

    auth = None
    if MCP_AUTH_USER and MCP_AUTH_PASSWORD:
        auth = httpx.BasicAuth(MCP_AUTH_USER, MCP_AUTH_PASSWORD)
        info(f"Using Basic Auth (user: {MCP_AUTH_USER})")
    else:
        warn("No authentication configured — connecting without credentials.")

    heading("MCP smoke-test client (mqacemcpserver)")
    info(f"Target: {SSE_URL}")

    # ----- Step 0: TCP+(optional)TLS handshake -----
    print(f"\n{BOLD}[0] Connectivity check{RESET}")
    parsed = urlparse(SSE_URL)
    connect_host = parsed.hostname or "127.0.0.1"
    connect_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    use_tls = parsed.scheme == "https"
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, connect_port, ssl=ctx),
                timeout=5.0,
            )
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(connect_host, connect_port),
                timeout=5.0,
            )
        writer.close()
        await writer.wait_closed()
        success(f"{'TLS' if use_tls else 'TCP'} handshake to {connect_host}:{connect_port} succeeded")
    except Exception as e:
        fail(f"Cannot reach {SSE_URL}")
        fail(f"  {type(e).__name__}: {e}")
        print(f"\n  Make sure the server is running:")
        print(f"    $env:MCP_TRANSPORT='sse'; .venv\\Scripts\\python.exe mqacemcpserver.py\n")
        return 1

    # ----- Step 1: open MCP session, list tools -----
    print(f"\n{BOLD}[1] Initialising MCP session …{RESET}")
    try:
        async with sse_client(
            SSE_URL,
            auth=auth,
            httpx_client_factory=_make_insecure_httpx_client,
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                success("MCP session initialised.")

                tools_result = await session.list_tools()
                tools = tools_result.tools
                tool_names = {t.name for t in tools}
                print(f"\n{BOLD}[2] Available tools ({len(tools)}){RESET}")
                for t in tools:
                    desc = (t.description or "").strip().split("\n")[0]
                    if len(desc) > 80:
                        desc = desc[:80] + "…"
                    print(f"  • {CYAN}{t.name}{RESET}: {desc}")

                missing = EXPECTED_TOOLS - tool_names
                extra = tool_names - EXPECTED_TOOLS
                if missing:
                    warn(f"Expected tools missing from server: {sorted(missing)}")
                if extra:
                    info(f"Server exposes additional tools not in test table: {sorted(extra)}")

                if list_only:
                    heading("--list mode: done")
                    return 0

                # ----- Step 2: call every tool -----
                channel = discover_channel_name()
                if channel:
                    info(f"Channel name from manifest: {channel}")
                else:
                    warn("No CHANNEL row found in resources/qmgr_dump.csv; using fallback.")
                table = build_tool_table(channel)
                if only:
                    table = [row for row in table if row["name"] == only]
                    if not table:
                        fail(f"--only {only!r} did not match any tool in the table.")
                        return 1

                results: list[tuple[str, str, str]] = []   # (name, outcome, reason)
                for i, row in enumerate(table, start=1):
                    name = row["name"]
                    args = row["args"]
                    mode = row["mode"]
                    print(f"\n{BOLD}[{2 + i}] {name}{RESET}  {GREY}({mode}){RESET}")
                    info(f"args: {json.dumps(args)}")
                    try:
                        result = await session.call_tool(name, args)
                        if result.content and getattr(result.content[0], "text", None):
                            output = result.content[0].text
                        else:
                            output = ""
                        preview(output)
                        outcome, reason = classify_output(output, mode)
                        results.append((name, outcome, reason))
                        if outcome == "pass":
                            success(f"{name} passed")
                        elif outcome == "skip":
                            warn(f"{name} skipped — {reason}")
                        else:
                            fail(f"{name} FAILED — {reason}")
                    except Exception as e:
                        msg = f"{type(e).__name__}: {e}"
                        fail(f"{name} raised: {msg}")
                        results.append((name, "fail", msg))

                # ----- Step 3: summary -----
                passed = sum(1 for _, o, _ in results if o == "pass")
                skipped = sum(1 for _, o, _ in results if o == "skip")
                failed = sum(1 for _, o, _ in results if o == "fail")

                heading(
                    f"Summary: {len(results)} tools  "
                    f"passed={passed}  skipped={skipped}  failed={failed}"
                )
                for name, outcome, reason in results:
                    if outcome == "pass":
                        success(f"{name:<28} pass")
                    elif outcome == "skip":
                        warn(f"{name:<28} skip — {reason}")
                    else:
                        fail(f"{name:<28} FAIL — {reason}")
                print()
                return 0 if failed == 0 else 1

    except Exception as e:
        if hasattr(e, "exceptions"):
            for ex in e.exceptions:
                fail(f"SSE connection error: {type(ex).__name__}: {ex}")
        else:
            fail(f"SSE connection error: {type(e).__name__}: {e}")
        return 1


def parse_args(argv: list[str]) -> tuple[str | None, bool]:
    only: str | None = None
    list_only = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--list":
            list_only = True
        elif a == "--only" and i + 1 < len(argv):
            only = argv[i + 1]
            i += 1
        elif a in ("-h", "--help"):
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown argument: {a!r}. Use --help for usage.")
            sys.exit(2)
        i += 1
    return only, list_only


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    only_arg, list_arg = parse_args(sys.argv[1:])
    sys.exit(asyncio.run(run(only=only_arg, list_only=list_arg)))
