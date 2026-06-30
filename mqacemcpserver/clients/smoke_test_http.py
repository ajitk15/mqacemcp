"""Streamable-HTTP smoke-test client for mqacemcpserver.

Identical in intent to `smoke_test.py` (lists tools, then exercises each
composite tool), but speaks the **Streamable HTTP** transport against the
`/mcp` endpoint instead of legacy SSE's `/sse`. Use this when the server runs
with `MCP_TRANSPORT=streamable-http` (the default).

All the test data and helpers (`CALLS`, `EXPECTED_TOOLS`, `classify`,
`select_calls`, `preview`, `heading`, the insecure httpx factory, and the
resolved credentials) are imported from `smoke_test` so the two clients never
drift. Run from inside `mqacemcpserver/clients/` or anywhere — the file adds its
own directory to `sys.path` so the sibling import resolves.

Usage (from the build folder, with the shared repo-root venv):
    ..\\.venv\\Scripts\\python.exe clients\\smoke_test_http.py            # all calls
    ..\\.venv\\Scripts\\python.exe clients\\smoke_test_http.py mq         # filter by category
    ..\\.venv\\Scripts\\python.exe clients\\smoke_test_http.py --full     # full output previews
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import urllib3

# Make the sibling `smoke_test` importable regardless of cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import smoke_test as st  # noqa: E402  (reuse CALLS, helpers, creds, factory)

# The streamable-http endpoint mirrors smoke_test's host/port but targets /mcp.
# Honour an explicit MCP_REMOTE_SERVER_URL override (e.g. behind a proxy).
_parsed = urlparse(st.SSE_URL)
_scheme = _parsed.scheme or "http"
MCP_URL = f"{_scheme}://{st.MCP_HOST}:{st.MCP_PORT}/mcp"


async def main():
    # Tool outputs contain emoji (🔍 ❌ ⚠️). Windows defaults to cp1252 which
    # cannot encode them, so reconfigure stdout to UTF-8 before any print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        print("FAIL: mcp SDK not installed in this venv")
        return 1

    auth = None
    if st.MCP_AUTH_USER and st.MCP_AUTH_PASSWORD:
        auth = httpx.BasicAuth(st.MCP_AUTH_USER, st.MCP_AUTH_PASSWORD)
        print(f"Basic Auth user={st.MCP_AUTH_USER}")

    st.heading(f"mqacemcpserver smoke ({MCP_URL})")

    async with streamablehttp_client(
        MCP_URL, auth=auth, httpx_client_factory=st._make_insecure_httpx_client
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

            missing = st.EXPECTED_TOOLS - names
            extra = names - st.EXPECTED_TOOLS
            if missing:
                print(f"  FAIL: missing tools: {sorted(missing)}")
                return 1
            if extra:
                print(f"  FAIL: unexpected tools: {sorted(extra)}")
                return 1
            print(f"  OK: catalogue == {len(st.EXPECTED_TOOLS)} expected tools")

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
            calls = st.select_calls(st.CALLS, selectors)
            if selectors:
                print(f"\n[Filter: {selectors} -> {len(calls)}/{len(st.CALLS)} calls]")
                if not calls:
                    print(f"  No calls match {selectors}. "
                          f"Use a category (mq/ace/cert) or a tool name.")
                    return 1

            results = []
            for i, (name, args, mode) in enumerate(calls, start=1):
                st.heading(f"[{i}] {name}  ({mode})  args={json.dumps(args)}")
                try:
                    res = await session.call_tool(name, args)
                    text = res.content[0].text if res.content and getattr(res.content[0], "text", None) else ""
                    st.preview(text, preview_limit)
                    outcome, reason = st.classify(text, mode)
                    results.append((i, name, mode, outcome, reason))
                    print(f"  -> {outcome}{(' (' + reason + ')') if reason else ''}")
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"
                    print(f"  RAISED: {msg}")
                    results.append((i, name, mode, "fail", msg))

            passed = sum(1 for *_, o, _ in results if o == "pass")
            skipped = sum(1 for *_, o, _ in results if o == "skip")
            failed = sum(1 for *_, o, _ in results if o == "fail")
            st.heading(f"Summary: pass={passed} skip={skipped} fail={failed} of {len(results)}")

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
