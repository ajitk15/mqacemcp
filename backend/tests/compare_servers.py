"""Benchmark the two MCP builds head-to-head through the chatbot backend.

Runs the SAME set of natural-language questions against each MCP server by
flipping the backend's active server via ``POST /api/mcp/connect`` between
runs, and records end-to-end performance per question (latency, number of tool
calls / round-trips, verdict). The output JSON feeds the dashboard's
"Compare" tab (``dashboard/analyze_logs.py:compute_comparison_html``).

Why end-to-end (through the LLM agent): the two builds are not tool-for-tool
comparable — ``mqacemcpserver`` exposes granular tools and usually needs several
calls to answer a question, while ``mqacemcpserver-single`` answers in one
composite call. Measuring through the agent captures that real difference.

Assumes the full stack is already running (both MCP builds, backend, dashboard).
Probes ``/api/health`` first and exits non-zero if unreachable.

Usage (from repo root, with the backend venv that has httpx):

  backend\\.venv\\Scripts\\python.exe backend\\tests\\compare_servers.py --limit 6
  ...\\python.exe backend\\tests\\compare_servers.py --only ace --out custom-logs\\compare_results.json
  ...\\python.exe backend\\tests\\compare_servers.py --servers https://localhost:8009/sse,https://localhost:8010/sse

NOTE: this drives the real LLM (costs tokens) and temporarily switches the
backend's active server. By default it restores the startup default server when
done; pass --no-restore to leave the last-tested server active.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

# Reuse the existing question-suite machinery (parsing + SSE driving).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_question_suite as suite  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = REPO_ROOT / "custom-logs" / "compare_results.json"


# ---------------------------------------------------------------------------
# Backend MCP-server control (endpoints added in the previous task)
# ---------------------------------------------------------------------------


def get_servers(client: httpx.Client, base: str) -> dict:
    r = client.get(f"{base}/api/mcp/servers", timeout=10.0)
    r.raise_for_status()
    return r.json()


def connect_server(client: httpx.Client, base: str, url: str) -> dict:
    """Switch the backend's active MCP server. Reloads tools + rebuilds the agent."""
    r = client.post(f"{base}/api/mcp/connect", json={"url": url}, timeout=120.0)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _percentile(values: list[float], pct: float) -> float:
    """Simple nearest-rank percentile; returns 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def _aggregate(results: list[dict]) -> dict:
    latencies = [r["latency_s"] for r in results]
    calls = [r["tool_calls"] for r in results]
    passes = sum(1 for r in results if r["verdict"] == "PASS")
    n = len(results)
    return {
        "questions": n,
        "pass": passes,
        "fail": n - passes,
        "pass_rate": round(passes / n * 100, 1) if n else 0.0,
        "mean_latency_s": round(statistics.fmean(latencies), 2) if latencies else 0.0,
        "median_latency_s": round(statistics.median(latencies), 2) if latencies else 0.0,
        "p95_latency_s": round(_percentile(latencies, 95), 2),
        "mean_tool_calls": round(statistics.fmean(calls), 2) if calls else 0.0,
        "total_tool_calls": sum(calls),
    }


# ---------------------------------------------------------------------------
# Run one server
# ---------------------------------------------------------------------------


def run_server(
    client: httpx.Client,
    base: str,
    url: str,
    name: str | None,
    questions: list,
    timeout_s: float,
) -> dict:
    print(f"\n=== Connecting backend to {name or url} ===", flush=True)
    conn = connect_server(client, base, url)
    if conn.get("status") != "ok":
        raise RuntimeError(f"connect to {url} failed: {conn.get('message')}")
    # Confirm what is actually active + capture metadata for the report.
    health = suite.health_probe(client, base)
    active_name = conn.get("active_name") or health.get("mcp_server_name") or url
    tool_count = conn.get("tool_count", health.get("tool_count"))
    print(
        f"    active: {active_name} · tools={tool_count} "
        f"· prompt={health.get('prompt_source')}",
        flush=True,
    )

    results: list[dict] = []
    for i, q in enumerate(questions, 1):
        r = suite.ask_question(client, base, q, timeout_s=timeout_s)
        record = {
            "id": q.id,
            "title": q.title,
            "domain": q.domain,
            "latency_s": round(r.elapsed_s, 2),
            "tool_calls": len(r.tool_calls),
            "tools": [rec.name for rec in r.tool_calls],
            "verdict": r.verdict,
            "reply_bytes": len(r.final_text or ""),
            "error": r.error,
        }
        results.append(record)
        print(
            f"    [{i}/{len(questions)}] {q.id} {r.verdict} "
            f"· {record['tool_calls']} call(s) · {record['latency_s']}s"
            + (f" · error: {r.error}" if r.error else ""),
            flush=True,
        )

    return {
        "name": active_name,
        "url": url,
        "tool_count": tool_count,
        "prompt_source": health.get("prompt_source"),
        "aggregate": _aggregate(results),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------


def print_comparison(servers: list[dict]) -> None:
    print("\n" + "=" * 64)
    print("COMPARISON SUMMARY")
    print("=" * 64)
    header = f"{'metric':<22}" + "".join(f"{s['name']:>22}" for s in servers)
    print(header)
    rows = [
        ("questions", "questions", None),
        ("pass rate %", "pass_rate", None),
        ("mean latency (s)", "mean_latency_s", "min"),
        ("median latency (s)", "median_latency_s", "min"),
        ("p95 latency (s)", "p95_latency_s", "min"),
        ("mean tool calls", "mean_tool_calls", "min"),
        ("total tool calls", "total_tool_calls", "min"),
    ]
    for label, key, _better in rows:
        line = f"{label:<22}" + "".join(
            f"{s['aggregate'][key]:>22}" for s in servers
        )
        print(line)
    print("=" * 64)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark MCP builds head-to-head via the chatbot backend."
    )
    parser.add_argument("--backend", default=suite.DEFAULT_BACKEND,
                        help=f"Chatbot backend base URL (default: {suite.DEFAULT_BACKEND})")
    parser.add_argument("--questions", default=str(suite.QUESTIONS_PATH),
                        help="Path to the questions markdown file")
    parser.add_argument("--servers", default="",
                        help="Comma-separated MCP SSE URLs to test (default: all from /api/mcp/servers)")
    parser.add_argument("--filter", default="",
                        help='Comma-separated question ids to run (e.g. "Q1,Q5")')
    parser.add_argument("--only", choices=("mq", "ace", "all"), default="all",
                        help="Limit to MQ-only, ACE-only, or all questions")
    parser.add_argument("--limit", type=int, default=6,
                        help="Cap the number of questions (after filter/only) to keep cost low. 0 = no cap.")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"JSON results output path (default: {DEFAULT_OUT})")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="Per-question timeout in seconds")
    parser.add_argument("--no-restore", action="store_true",
                        help="Do not re-activate the startup default server when done")
    args = parser.parse_args(argv)

    base = args.backend.rstrip("/")

    questions = suite.parse_questions(Path(args.questions))
    if args.filter:
        wanted = {q.strip().upper() for q in args.filter.split(",") if q.strip()}
        questions = [q for q in questions if q.id.upper() in wanted]
    if args.only != "all":
        questions = [q for q in questions if q.domain == args.only]
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]
    if not questions:
        print("ERROR: no questions matched filter/only/limit", file=sys.stderr)
        return 2

    with httpx.Client() as client:
        # Health + registry discovery.
        try:
            suite.health_probe(client, base)
            registry = get_servers(client, base)
        except Exception as err:  # noqa: BLE001
            print(f"ERROR: backend not reachable at {base}: {err}", file=sys.stderr)
            return 3

        known = registry.get("servers") or []
        default_url = (registry.get("active_url") or "").strip()  # current = startup default
        url_to_name = {(s.get("url") or "").strip(): s.get("name") for s in known}

        if args.servers:
            targets = [u.strip() for u in args.servers.split(",") if u.strip()]
        else:
            targets = [(s.get("url") or "").strip() for s in known if s.get("url")]
        if len(targets) < 2:
            print(
                f"WARNING: only {len(targets)} server(s) to compare "
                f"(need 2 for a head-to-head). Proceeding anyway.",
                file=sys.stderr,
            )
        if not targets:
            print("ERROR: no MCP servers to test", file=sys.stderr)
            return 2

        print(
            f"backend={base} · servers={len(targets)} · questions={len(questions)} "
            f"(timeout={args.timeout}s each)"
        )

        server_reports: list[dict] = []
        try:
            for url in targets:
                server_reports.append(
                    run_server(client, base, url, url_to_name.get(url), questions, args.timeout)
                )
        finally:
            # Restore the startup default so the UI is left as it was.
            if not args.no_restore and default_url:
                try:
                    print(f"\n=== Restoring default server {url_to_name.get(default_url) or default_url} ===")
                    connect_server(client, base, default_url)
                except Exception as err:  # noqa: BLE001
                    print(f"WARNING: could not restore default server: {err}", file=sys.stderr)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "backend": base,
        "questions_total": len(questions),
        "servers": server_reports,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print_comparison(server_reports)
    print(f"\nWrote {out_path}")
    print("Open the dashboard's Compare tab to view the side-by-side report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
