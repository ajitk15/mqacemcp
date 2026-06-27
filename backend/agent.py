"""LangGraph ReAct agent + in-process session memory.

The agent itself is generic. It receives a system prompt that's auto-built
from whichever MCP tools were loaded — there are no tool names hardcoded
here. To target a different MCP server, change MCP_SSE_URL in .env.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

log = logging.getLogger("chatbot.agent")

_REQUIRED_PLACEHOLDERS = ("{scope_block}", "{tool_catalog}")


SCOPE_BLOCK_TEMPLATE = """\
SCOPE: You ONLY answer questions about {bot_domain}. If the user asks
something clearly outside that scope (other products, general knowledge,
weather, personal advice, unrelated code, small talk, etc.), do NOT call
any tools. Reply with exactly:

  "I can only help with questions about {bot_domain}. Try asking about
   queue depths, channel status, integration nodes, message flows,
   certificate expiry, etc.
   For anything else, please reach out to the **{support_team}** team."

When in doubt about whether a question is in scope, assume it IS in scope
and proceed (call the relevant tool or ask one targeted clarifying
question). Do NOT fire the refusal for IBM MQ / IBM ACE feature names
(SSL key repository, channel, trigger, model queue, transmission queue,
JMS, AMQP, BIP codes, etc.) or for TLS/SSL certificate inventory questions
(expiry, validity dates, common name / CN, certificate alias) — those are
in scope.

"""


SYSTEM_PROMPT_TEMPLATE = """\
You are an operations assistant connected to an MCP server. The tools you
have are listed below — pick the one whose description best matches the
user's question, call it, then explain the result.

{scope_block}Formatting rules for your final reply:
- Be concise. Start with a one-sentence answer.
- When the tool returns a list or table, the UI will render it as a table
  automatically. You do not need to repeat the rows in prose.
- When the user is asking about relationships, hierarchies, or how things
  connect (parent/child, source/target, alias→target, server→app→flow),
  include a Mermaid diagram in your reply using a fenced code block:
      ```mermaid
      flowchart LR
        A[Alias] --> B[Target]
      ```
  Keep diagrams small (<= 12 nodes).
- For follow-up questions, remember context from the same conversation
  (queue manager name, node, server, etc.) so the user does not have to
  repeat it.
- Never invent tool names, arguments, or output. If a tool returns an
  error, surface it plainly.

Available tools:
{tool_catalog}
"""


def _format_tool_catalog(tools: list[BaseTool]) -> str:
    lines: list[str] = []
    for tool in tools:
        desc = (tool.description or "").strip().splitlines()[0] if tool.description else ""
        lines.append(f"- {tool.name}: {desc}")
    return "\n".join(lines) if lines else "(no tools loaded)"


_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_system_prompt_template(prompt_file: str | None = None) -> tuple[str, str]:
    """Return (template_text, source_label).

    Resolution order:
      1. Explicit ``prompt_file`` arg (per-server override), if it exists.
      2. Path in SYSTEM_PROMPT_FILE env, if set and the file exists.
      3. backend/prompts/system.md next to this module.
      4. Inline SYSTEM_PROMPT_TEMPLATE (always works, last resort).

    Relative ``prompt_file`` paths resolve against the repo root so a registry
    entry like "backend/prompts/system.md" works regardless of cwd.

    Any candidate missing the required placeholders is rejected with a
    warning so an editing mistake can never crash the backend at startup.
    """
    candidates: list[Path] = []
    if prompt_file and prompt_file.strip():
        p = Path(prompt_file.strip())
        candidates.append(p if p.is_absolute() else (_REPO_ROOT / p))
    explicit = os.getenv("SYSTEM_PROMPT_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path(__file__).parent / "prompts" / "system.md")

    for path in candidates:
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError as err:
            log.warning("Could not read prompt file %s: %s", path, err)
            continue
        missing = [p for p in _REQUIRED_PLACEHOLDERS if p not in text]
        if missing:
            log.warning(
                "Prompt file %s is missing placeholders %s; skipping.",
                path,
                missing,
            )
            continue
        return text, str(path)

    return SYSTEM_PROMPT_TEMPLATE, "inline"


def _render_system_prompt(
    template: str,
    scope_block: str,
    tool_catalog: str,
    support_team: str,
) -> str:
    """Substitute placeholders without breaking on stray '{' in markdown."""
    return (
        template
        .replace("{scope_block}", scope_block)
        .replace("{tool_catalog}", tool_catalog)
        .replace("{support_team}", support_team)
    )


def build_agent(
    tools: list[BaseTool], prompt_file: str | None = None
) -> tuple[object, MemorySaver]:
    """Build the LangGraph agent. Returns (agent, checkpointer).

    ``prompt_file`` optionally overrides the system-prompt template (used to
    match a per-server prompt to the active MCP server's tool set).

    The checkpointer is returned so `app.py` can clear a thread on /reset.
    """
    model_name = os.getenv("OPENAI_MODEL", "gpt-5.5")
    llm = ChatOpenAI(model=model_name, temperature=0, streaming=True)

    bot_domain = os.getenv("BOT_DOMAIN", "").strip()
    support_team = os.getenv("SUPPORT_TEAM", "").strip() or "MQ_ACE_SUPPORT"
    scope_block = (
        SCOPE_BLOCK_TEMPLATE.format(
            bot_domain=bot_domain, support_team=support_team
        )
        if bot_domain
        else ""
    )

    template, prompt_source = _load_system_prompt_template(prompt_file)
    system_prompt = _render_system_prompt(
        template,
        scope_block=scope_block,
        tool_catalog=_format_tool_catalog(tools),
        support_team=support_team,
    )

    checkpointer = MemorySaver()
    agent = create_react_agent(
        model=llm,
        tools=tools,
        checkpointer=checkpointer,
        prompt=system_prompt,
    )
    log.info(
        "Agent built with model=%s, tools=%d, scope=%s, prompt=%s",
        model_name,
        len(tools),
        bot_domain or "(unrestricted)",
        prompt_source,
    )
    return agent, checkpointer


def get_prompt_source(prompt_file: str | None = None) -> str:
    """Public helper for /api/health to surface the resolved prompt source."""
    _, source = _load_system_prompt_template(prompt_file)
    return source
