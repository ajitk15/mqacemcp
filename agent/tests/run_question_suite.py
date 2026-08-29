"""Pump the questions from MQ_ACE_Chatbot_Questions.md (beside this script)
at the chatbot backend and produce a markdown report.

Assumes the chatbot backend is already running. Probes /api/health first
and exits non-zero if unreachable.

Usage (from repo root, with either venv that has httpx):

  python agent/tests/run_question_suite.py --out report.md
  python agent/tests/run_question_suite.py --filter Q1,Q5 --out smoke.md
  python agent/tests/run_question_suite.py --only ace --out ace.md
  python agent/tests/run_question_suite.py \
      --questions agent/tests/ACE_Complex_Questions.md --out ace-complex.md

A question block looks like:

  **CX1 - Title**
  > "the question text"

  *Expected answer area:* prose describing a good answer.
  *Expected tools:* ace_search            # or `none` to expect NO tool call
  *Must mention:* NODE1, NODE2, AmazonS3  # case-insensitive substrings
  *Must not mention:* ACE_DEMO_CACHE      # hallucination guard

Only the quoted question line is required. When a block declares no
`*Expected tools:*` / `*Must mention:*` / `*Must not mention:*`, it is scored
with the legacy rule (PASS = no error and at least one tool call).
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
QUESTIONS_PATH = Path(__file__).resolve().parent / "MQ_ACE_Chatbot_Questions.md"
DEFAULT_BACKEND = os.getenv("MCP_BACKEND_URL", "").strip() or "http://localhost:8002"

PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"


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
    expected_tools: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    must_not_mention: list[str] = field(default_factory=list)

    @property
    def expects_no_tool(self) -> bool:
        """`none` alone means a tool call is a failure."""
        return [t.lower() for t in self.expected_tools] == ["none"]

    @property
    def allows_no_tool(self) -> bool:
        """`none` alongside tool names means zero calls is ALSO acceptable.

        Used where either behaviour is defensible — e.g. a question about data
        no tool exposes, which the assistant may decline outright or may look
        up first and then report as unavailable.
        """
        return "none" in {t.lower() for t in self.expected_tools}

    @property
    def has_assertions(self) -> bool:
        return bool(
            self.expected_tools or self.must_mention or self.must_not_mention
        )


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
    tool_mismatch: str | None = None
    missing_tokens: list[str] = field(default_factory=list)
    forbidden_tokens: list[str] = field(default_factory=list)

    def evaluate(self) -> str:
        """Compute the verdict and populate the per-check detail fields."""
        q = self.question
        self.tool_mismatch = None
        self.missing_tokens = []
        self.forbidden_tokens = []

        if self.error:
            return FAIL

        called = [rec.name for rec in self.tool_calls]

        if q.expects_no_tool:
            if called:
                self.tool_mismatch = (
                    f"expected no tool call, got {', '.join(called)}"
                )
                return FAIL
        elif q.expected_tools:
            wanted = {t.lower() for t in q.expected_tools}
            # `none` listed alongside real tools: zero calls is acceptable too.
            if not (q.allows_no_tool and not called) and not any(
                name.lower() in wanted for name in called
            ):
                self.tool_mismatch = (
                    f"expected {'/'.join(q.expected_tools)}, "
                    f"got {', '.join(called) or '(none)'}"
                )
                return FAIL
        elif not called:
            # Legacy rule: a question with no declared tool expectation still
            # has to have called something.
            self.tool_mismatch = "no tool called"
            return FAIL

        haystack = (self.final_text or "").lower()
        self.missing_tokens = [
            tok for tok in q.must_mention if tok.lower() not in haystack
        ]
        self.forbidden_tokens = [
            tok for tok in q.must_not_mention if tok.lower() in haystack
        ]
        if self.missing_tokens or self.forbidden_tokens:
            return PARTIAL
        return PASS

    @property
    def verdict(self) -> str:
        return self.evaluate()

    @property
    def checks_summary(self) -> str:
        parts: list[str] = []
        if self.tool_mismatch:
            parts.append(f"tool: {self.tool_mismatch}")
        if self.missing_tokens:
            parts.append("missing: " + ", ".join(self.missing_tokens))
        if self.forbidden_tokens:
            parts.append("forbidden: " + ", ".join(self.forbidden_tokens))
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# Question parsing
# ---------------------------------------------------------------------------

_Q_HEADER = re.compile(r"^\*\*([A-Za-z]+\d+)\s*[—\-]\s*(.+?)\*\*\s*$")
_QUOTE = re.compile(r'^>\s*"(.+?)"\s*$')
_EXPECTED = re.compile(r"^\*Expected answer area:\*\s*(.*)$")
_EXPECTED_TOOLS = re.compile(r"^\*Expected tools:\*\s*(.*)$")
_MUST_MENTION = re.compile(r"^\*Must mention:\*\s*(.*)$")
_MUST_NOT_MENTION = re.compile(r"^\*Must not mention:\*\s*(.*)$")
_SECTION = re.compile(r"^###\s+(.+?)\s*$")
_TOP_SECTION = re.compile(r"^##\s+IBM\s+(MQ|ACE)\b", re.IGNORECASE)

_TRAILING_DIGITS = re.compile(r"(\d+)$")


def _split_list(raw: str) -> list[str]:
    """Split a comma-separated field, dropping blanks and backtick quoting."""
    out: list[str] = []
    for part in (raw or "").split(","):
        item = part.strip().strip("`").strip()
        if item:
            out.append(item)
    return out


def _flush(pending: dict | None, questions: list[Question]) -> None:
    if not pending or not pending.get("question"):
        return
    m_n = _TRAILING_DIGITS.search(pending["id"])
    questions.append(
        Question(
            id=pending["id"],
            n=int(m_n.group(1)) if m_n else 0,
            title=pending["title"],
            category=pending["category"],
            question=pending["question"],
            expected=pending["expected"],
            domain=pending["domain"],
            expected_tools=pending["expected_tools"],
            must_mention=pending["must_mention"],
            must_not_mention=pending["must_not_mention"],
        )
    )


def parse_questions(path: Path) -> list[Question]:
    """Extract every `**<ID> - <Title>**` block from a questions markdown file.

    A block is finalised when the next block/section header arrives, or at
    end of file - so optional fields may follow `*Expected answer area:*`.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    questions: list[Question] = []
    current_category = "(uncategorised)"
    current_domain = "mq"
    pending: dict | None = None

    for line in lines:
        m_top = _TOP_SECTION.match(line)
        if m_top:
            _flush(pending, questions)
            pending = None
            current_domain = "ace" if m_top.group(1).upper() == "ACE" else "mq"
            continue
        m_sec = _SECTION.match(line)
        if m_sec:
            _flush(pending, questions)
            pending = None
            current_category = m_sec.group(1)
            continue
        m_q = _Q_HEADER.match(line)
        if m_q:
            _flush(pending, questions)
            pending = {
                "id": m_q.group(1),
                "title": m_q.group(2).strip(),
                "category": current_category,
                "domain": current_domain,
                "question": "",
                "expected": "",
                "expected_tools": [],
                "must_mention": [],
                "must_not_mention": [],
            }
            continue
        if pending is None:
            continue

        stripped = line.strip()
        m_quote = _QUOTE.match(stripped)
        if m_quote and not pending["question"]:
            pending["question"] = m_quote.group(1).strip()
            continue
        m_exp = _EXPECTED.match(line)
        if m_exp:
            pending["expected"] = m_exp.group(1).strip()
            continue
        m_tools = _EXPECTED_TOOLS.match(line)
        if m_tools:
            pending["expected_tools"] = _split_list(m_tools.group(1))
            continue
        m_must = _MUST_MENTION.match(line)
        if m_must:
            pending["must_mention"] = _split_list(m_must.group(1))
            continue
        m_not = _MUST_NOT_MENTION.match(line)
        if m_not:
            pending["must_not_mention"] = _split_list(m_not.group(1))
            continue

    _flush(pending, questions)
    return questions


def parse_question_files(paths: Iterable[Path]) -> list[Question]:
    """Parse several question files, preserving order and reporting collisions."""
    out: list[Question] = []
    seen: dict[str, str] = {}
    for p in paths:
        for q in parse_questions(p):
            if q.id in seen:
                print(
                    f"WARNING: duplicate question id {q.id} "
                    f"({seen[q.id]} and {p}); the later one is kept",
                    file=sys.stderr,
                )
            seen[q.id] = str(p)
            out.append(q)
    return out


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
    verdicts = {r.question.id: r.verdict for r in results}
    pass_count = sum(1 for v in verdicts.values() if v == PASS)
    partial_count = sum(1 for v in verdicts.values() if v == PARTIAL)
    fail_count = sum(1 for v in verdicts.values() if v == FAIL)
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
        f"**PASS {pass_count}  PARTIAL {partial_count}  FAIL {fail_count}  "
        f"TOTAL {total}  ({pct:.0f}% clean)**"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Q   | Category                       | Tools called                              | Verdict | Checks |"
    )
    lines.append(
        "| --- | ------------------------------ | ----------------------------------------- | ------- | ------ |"
    )
    for r in results:
        chain = " → ".join(rec.name for rec in r.tool_calls) or "_(none)_"
        cat = _truncate(r.question.category, 30)
        chain = _truncate(chain, 41)
        checks = _truncate(r.checks_summary, 60) or "—"
        lines.append(
            f"| {r.question.id} | {cat} | {chain} | "
            f"{verdicts[r.question.id]} | {checks} |"
        )
    lines.append("")
    lines.append("## Per-question detail")
    lines.append("")
    for r in results:
        lines.append(
            f"### {r.question.id} — {r.question.title} "
            f"({verdicts[r.question.id]})"
        )
        lines.append("")
        lines.append(f"**Question:** {r.question.question}")
        lines.append("")
        if r.question.expected:
            lines.append(f"**Expected:** {r.question.expected}")
            lines.append("")
        if r.question.has_assertions:
            asserted: list[str] = []
            if r.question.expected_tools:
                asserted.append(
                    "tools=" + ", ".join(f"`{t}`" for t in r.question.expected_tools)
                )
            if r.question.must_mention:
                asserted.append(
                    "must mention=" + ", ".join(r.question.must_mention)
                )
            if r.question.must_not_mention:
                asserted.append(
                    "must not mention=" + ", ".join(r.question.must_not_mention)
                )
            lines.append("**Asserted:** " + " · ".join(asserted))
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
        checks = r.checks_summary
        if checks:
            lines.append(f"**Checks:** {checks}")
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
        help=(
            "Path to the questions markdown file. Accepts a comma-separated "
            "list to run several files in one report."
        ),
    )
    parser.add_argument(
        "--filter",
        default="",
        help='Comma-separated question ids to run (e.g. "Q1,CX5"). Default: all.',
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

    paths = [Path(p.strip()) for p in args.questions.split(",") if p.strip()]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print(
            "ERROR: questions file(s) not found: "
            + ", ".join(str(p) for p in missing),
            file=sys.stderr,
        )
        return 2

    questions = parse_question_files(paths)
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
            verdict = r.verdict
            print(
                f"    {verdict} · {len(r.tool_calls)} tool(s) "
                f"· {r.elapsed_s:.1f}s"
            )
            if r.checks_summary:
                print(f"    checks: {r.checks_summary}")
            if r.error:
                print(f"    error: {r.error}")

    out_path = Path(args.out)
    write_report(out_path, base, health, results, started_at)
    verdicts = [r.verdict for r in results]
    pass_count = verdicts.count(PASS)
    partial_count = verdicts.count(PARTIAL)
    fail_count = verdicts.count(FAIL)
    print(
        f"\nWrote {out_path} · PASS {pass_count} · "
        f"PARTIAL {partial_count} · FAIL {fail_count} of {len(results)}"
    )
    return 0 if pass_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
