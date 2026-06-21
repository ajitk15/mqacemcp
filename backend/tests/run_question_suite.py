"""Pump the 30 questions from docs/MQ_ACE_Chatbot_Questions.md at the
chatbot backend and produce a markdown report.

Assumes the chatbot backend is already running. Probes /api/health first
and exits non-zero if unreachable.

Usage (from repo root, with either venv that has httpx):

  python backend/tests/run_question_suite.py --out report.md
  python backend/tests/run_question_suite.py --filter Q1,Q5 --out smoke.md
  python backend/tests/run_question_suite.py --only ace --out ace.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
QUESTIONS_PATH = REPO_ROOT / "docs" / "MQ_ACE_Chatbot_Questions.md"
DEFAULT_BACKEND = os.getenv("MCP_BACKEND_URL", "").strip() or "http://localhost:8002"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Question:
    id: str
    n: int
    title: str
    category: str
    question: str
    expected: str
    domain: str


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    call_id: str | None = None
    result_summary: str | None = None
    result_bytes: int | None = None


@dataclass
class QuestionResult:
    question: Question
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    final_text: str = ""
    error: str | None = None
    elapsed_s: float = 0.0

    @property
    def verdict(self) -> str:
        if self.error:
            return "FAIL"
        if not self.tool_calls:
            return "FAIL"
        return "PASS"


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------

_Q_HEADER = re.compile(r"^\*\*(Q\d+)\s*[—\-]\s*(.+?)\*\*\s*$")
_QUOTE = re.compile(r'^>\s*"(.+?)"\s*$')
_EXPECTED = re.compile(r"^\*Expected answer area:\*\s*(.*)$")
_SECTION = re.compile(r"^###\s+(.+?)\s*$")
_TOP_SECTION = re.compile(r"^##\s+IBM\s+(MQ|ACE)\b", re.IGNORECASE)


def parse_questions(path: Path) -> list[Question]:
    """Extract every `**Q<N> — <Title>**` block from the questions markdown."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    questions: list[Question] = []
    current_category = "(uncategorised)"
    current_domain = "mq"
    pending: dict[str, str] | None = None

    for line in lines:
        m_top = _TOP_SECTION.match(line)
        if m_top:
            current_domain = "ace" if m_top.group(1).upper() == "ACE" else "mq"
            continue
        m_sec = _SECTION.match(line)
        if m_sec:
            current_category = m_sec.group(1)
            continue
        m_q = _Q_HEADER.match(line)
        if m_q:
            pending = {
                "id": m_q.group(1),
                "title": m_q.group(2).strip(),
                "category": current_category,
                "domain": current_domain,
                "question": "",
                "expected": "",
            }
            continue
        if pending is not None:
            m_quote = _QUOTE.match(line.strip())
            if m_quote and not pending["question"]:
                pending["question"] = m_quote.group(1).strip()
                continue
            m_exp = _EXPECTED.match(line)
            if m_exp:
                pending["expected"] = m_exp.group(1).strip()
                questions.append(
                    Question(
                        id=pending["id"],
                        n=int(pending["id"][1:]),
                        title=pending["title"],
                        category=pending["category"],
                        question=pending["question"],
                        expected=pending["expected"],
                        domain=pending["domain"],
                    )
                )
                pending = None
    return questions


# ---------------------------------------------------------------------------
# Backend interaction
# ---------------------------------------------------------------------------


def health_probe(client: httpx.Client, base: str) -> dict:
    r = client.get(f"{base}/api/health", timeout=10.0)
    r.raise_for_status()
    return r.json()


def reset_thread(client: httpx.Client, base: str, thread_id: str) -> None:
    try:
        client.post(
            f"{base}/api/chat/reset",
            json={"thread_id": thread_id},
            timeout=5.0,
        )
    except Exception:
        pass


def _parse_sse_events(text_stream: Iterable[str]) -> Iterable[dict]:
    """Yield decoded JSON event dicts from an SSE text stream."""
    buffer = ""
    for chunk in text_stream:
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            event_block, buffer = buffer.split("\n\n", 1)
            for line in event_block.splitlines():
                if line.startswith("data: "):
                    payload = line[len("data: "):]
                    try:
                        yield json.loads(payload)
                    except json.JSONDecodeError:
                        continue


def ask_question(
    client: httpx.Client,
    base: str,
    q: Question,
    timeout_s: float,
) -> QuestionResult:
    thread_id = str(uuid.uuid4())
    reset_thread(client, base, thread_id)

    result = QuestionResult(question=q)
    pending_calls: dict[str, ToolCallRecord] = {}

    started = time.monotonic()
    try:
        with client.stream(
            "POST",
            f"{base}/api/chat/stream",
            json={"message": q.question, "thread_id": thread_id},
            timeout=timeout_s,
        ) as resp:
            resp.raise_for_status()
            for evt in _parse_sse_events(resp.iter_text()):
                kind = evt.get("kind")
                if kind == "token":
                    result.final_text += evt.get("text", "")
                elif kind == "tool_call":
                    rec = ToolCallRecord(
                        name=evt.get("name", "?"),
                        args=evt.get("args", {}) or {},
                        call_id=evt.get("call_id"),
                    )
                    result.tool_calls.append(rec)
                    if rec.call_id:
                        pending_calls[rec.call_id] = rec
                elif kind == "tool_result":
                    block = evt.get("block", {}) or {}
                    raw = (
                        block.get("text")
                        or block.get("code")
                        or block.get("mermaid")
                        or ""
                    )
                    snippet = (raw or "").strip()
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "…"
                    cid = evt.get("call_id")
                    rec = pending_calls.get(cid) if cid else None
                    if rec is None:
                        for candidate in reversed(result.tool_calls):
                            if (
                                candidate.name == evt.get("name")
                                and candidate.result_summary is None
                            ):
                                rec = candidate
                                break
                    if rec is not None:
                        rec.result_summary = snippet
                        rec.result_bytes = len(raw or "")
                elif kind == "error":
                    result.error = evt.get("message", "unknown")
                elif kind == "done":
                    break
    except httpx.HTTPStatusError as err:
        body = ""
        try:
            body = err.response.text[:200]
        except Exception:
            pass
        result.error = f"HTTP {err.response.status_code}: {body}"
    except httpx.RequestError as err:
        result.error = f"Request error: {err}"
    except Exception as err:  # noqa: BLE001
        result.error = f"{type(err).__name__}: {err}"
    finally:
        result.elapsed_s = time.monotonic() - started

    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"


def _fmt_args(args: dict) -> str:
    parts: list[str] = []
    for k, v in (args or {}).items():
        sv = v if isinstance(v, str) else json.dumps(v)
        if len(sv) > 40:
            sv = sv[:40] + "…"
        if isinstance(v, str):
            parts.append(f'{k}="{sv}"')
        else:
            parts.append(f"{k}={sv}")
    return ", ".join(parts)


def write_report(
    out_path: Path,
    base: str,
    health: dict,
    results: list[QuestionResult],
    started_at: str,
) -> None:
    pass_count = sum(1 for r in results if r.verdict == "PASS")
    total = len(results)
    pct = (pass_count / total * 100) if total else 0

    lines: list[str] = []
    lines.append("# Chatbot Question Suite Report")
    lines.append("")
    lines.append(
        f"_{started_at} · backend=`{base}` · "
        f"tools={health.get('tool_count', '?')} · "
        f"prompt=`{health.get('prompt_source', '?')}`_"
    )
    lines.append("")
    lines.append(
        f"**PASS {pass_count}  FAIL {total - pass_count}  TOTAL {total}  "
        f"({pct:.0f}%)**"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Q   | Category                       | Tools called                              | Verdict |"
    )
    lines.append(
        "| --- | ------------------------------ | ----------------------------------------- | ------- |"
    )
    for r in results:
        chain = " → ".join(rec.name for rec in r.tool_calls) or "_(none)_"
        cat = _truncate(r.question.category, 30)
        chain = _truncate(chain, 41)
        lines.append(f"| {r.question.id} | {cat} | {chain} | {r.verdict} |")
    lines.append("")
    lines.append("## Per-question detail")
    lines.append("")
    for r in results:
        lines.append(
            f"### {r.question.id} — {r.question.title} ({r.verdict})"
        )
        lines.append("")
        lines.append(f"**Question:** {r.question.question}")
        lines.append("")
        if r.question.expected:
            lines.append(f"**Expected:** {r.question.expected}")
            lines.append("")
        if r.tool_calls:
            lines.append("**Tool sequence:**")
            for i, rec in enumerate(r.tool_calls, 1):
                args_str = _fmt_args(rec.args)
                size = (
                    f" → {rec.result_bytes} bytes"
                    if rec.result_bytes is not None
                    else ""
                )
                lines.append(f"  {i}. `{rec.name}({args_str})`{size}")
            lines.append("")
        else:
            lines.append("**Tool sequence:** _(none observed)_")
            lines.append("")
        if r.error:
            lines.append(f"**Error:** `{r.error}`")
            lines.append("")
        reply = _truncate(r.final_text, 800)
        if reply:
            lines.append("**Final reply:**")
            for line in reply.splitlines():
                lines.append(f"> {line}")
            lines.append("")
        else:
            lines.append("**Final reply:** _(empty)_")
            lines.append("")
        lines.append(f"_elapsed={r.elapsed_s:.1f}s_")
        lines.append("")
        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the chatbot question suite against a live backend."
    )
    parser.add_argument(
        "--backend",
        default=DEFAULT_BACKEND,
        help=f"Chatbot backend base URL (default: {DEFAULT_BACKEND})",
    )
    parser.add_argument(
        "--questions",
        default=str(QUESTIONS_PATH),
        help="Path to the questions markdown file",
    )
    parser.add_argument(
        "--filter",
        default="",
        help='Comma-separated question ids to run (e.g. "Q1,Q5"). Default: all.',
    )
    parser.add_argument(
        "--only",
        choices=("mq", "ace", "all"),
        default="all",
        help="Limit to MQ-only, ACE-only, or all questions",
    )
    parser.add_argument(
        "--out",
        default="chatbot-question-report.md",
        help="Markdown report output path",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-question timeout in seconds",
    )
    args = parser.parse_args(argv)

    questions = parse_questions(Path(args.questions))
    if not questions:
        print(
            f"ERROR: no questions parsed from {args.questions}",
            file=sys.stderr,
        )
        return 2

    if args.filter:
        wanted = {q.strip().upper() for q in args.filter.split(",") if q.strip()}
        questions = [q for q in questions if q.id.upper() in wanted]
    if args.only != "all":
        questions = [q for q in questions if q.domain == args.only]
    if not questions:
        print("ERROR: no questions matched filter/only", file=sys.stderr)
        return 2

    base = args.backend.rstrip("/")

    with httpx.Client() as client:
        try:
            health = health_probe(client, base)
        except Exception as err:  # noqa: BLE001
            print(
                f"ERROR: backend health probe failed at {base}/api/health: {err}",
                file=sys.stderr,
            )
            return 3

        print(
            f"backend OK: {base} · tools={health.get('tool_count')} "
            f"· prompt={health.get('prompt_source')}"
        )
        print(
            f"running {len(questions)} question(s) "
            f"(timeout={args.timeout}s per question)"
        )

        results: list[QuestionResult] = []
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        for i, q in enumerate(questions, 1):
            print(f"[{i}/{len(questions)}] {q.id} — {q.title}", flush=True)
            r = ask_question(client, base, q, timeout_s=args.timeout)
            results.append(r)
            print(
                f"    {r.verdict} · {len(r.tool_calls)} tool(s) "
                f"· {r.elapsed_s:.1f}s"
            )
            if r.error:
                print(f"    error: {r.error}")

    out_path = Path(args.out)
    write_report(out_path, base, health, results, started_at)
    pass_count = sum(1 for r in results if r.verdict == "PASS")
    print(f"\nWrote {out_path} · {pass_count}/{len(results)} PASS")
    return 0 if pass_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
