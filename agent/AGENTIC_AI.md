# Why this is "Agentic AI"

This document answers one specific question that keeps coming up:

> *Why do we call this an agentic AI system? It's a chat box that calls
> some APIs — what makes it agentic?*

Short answer: because the LLM **decides what to do**, not the developer.
Every turn, the model is given a goal (the user's question), a toolbox (the
MCP tools), and a memory (the prior turns), and it autonomously plans and
executes a sequence of actions to reach the goal — including choosing
*which* tool to call, *what arguments* to pass, *whether to call more tools
based on the result*, and *when to stop*. There is no hand-written
if/then dispatcher anywhere in the codebase.

The rest of this doc maps the canonical agentic-AI components to specific
files in this repo so the claim is verifiable.

---

## What "agentic AI" actually means

There is no ISO definition. The working definition used across the industry
(Anthropic's "Building effective agents", LangChain/LangGraph blog posts,
OpenAI's Agents SDK guide, Lilian Weng's "LLM Powered Autonomous Agents")
converges on a system where:

1. **An LLM is the central reasoner**, deciding the next step rather than
   following a fixed script.
2. **Goal-directed**: it pursues a user's objective across multiple steps,
   not just one prompt → one reply.
3. **Tool-using**: it can invoke external functions / APIs / retrievers
   to gather information or take actions in the world.
4. **Has memory** so it can build on prior context.
5. **Operates in a loop** (observe → think → act → observe) until the
   goal is reached or a stop condition fires.
6. **Constrained by guardrails** (scope, safety, allow-lists) so its
   autonomy is bounded.

A system missing #1 (e.g., a deterministic API gateway with an LLM bolted
on for natural-language input) is *not* agentic. A system with all six is
agentic, regardless of whether it's a single agent or a swarm.

---

## The standard components of an agentic system

Pulled from the sources above. This is the checklist most architects use
when reviewing whether something qualifies.

| # | Component | What it does |
|---|---|---|
| 1 | **LLM reasoner** | Picks the next action; converts text into structured tool calls. |
| 2 | **Tool registry** | The set of capabilities the LLM is allowed to invoke. |
| 3 | **Tool selector / planner** | Logic that maps user intent → tool choice. In modern stacks the LLM does this itself, guided by tool descriptions. |
| 4 | **Action loop** | Repeats {observe → think → act} until the agent decides to stop. ReAct, ReWOO, plan-and-execute are common patterns. |
| 5 | **Short-term memory** | The current conversation context (LLM context window). |
| 6 | **Long-term / session memory** | Persisted state across turns or sessions (checkpointer, vector store, key-value store). |
| 7 | **Output formatter** | Structured output, JSON-mode, schema-bound responses, or post-processing into UI primitives. |
| 8 | **Guardrails** | Scope restriction, content moderation, tool allow/deny, refusal handling, max-iteration caps. |
| 9 | **Observability** | Tracing each step (which tool, what args, what response, latency). |
| 10 | **Streaming I/O** | Token-by-token output and incremental tool-call events for low-latency UX. |
| 11 | **Multi-agent coordination** *(advanced)* | Multiple specialised agents handing work to each other. Optional. |
| 12 | **Human-in-the-loop** *(advanced)* | Pausing for approval before destructive actions. Optional. |

---

## How this solution implements each component

| # | Component | This solution | File / line | Status |
|---|---|---|---|---|
| 1 | LLM reasoner | OpenAI GPT-5.5 via `langchain-openai` | `agent/agent.py` (`ChatOpenAI(model=…)`) | ✅ |
| 2 | Tool registry | 14 read-only IBM MQ + ACE + certificate tools loaded over MCP | `agent/mcp_client.py` (`load_tools` → `MultiServerMCPClient.get_tools`) | ✅ |
| 3 | Tool selector | The LLM itself, guided by tool docstrings (`IBM MQ:` / `IBM ACE:` / `Certificate:` prefix). No dispatcher code. | tool descriptions in `mqacemcpserver/server/composite_tools.py`; auto-formatted into the system prompt by `_format_tool_catalog` in `agent.py` | ✅ |
| 4 | Action loop | `langgraph.prebuilt.create_react_agent` — full ReAct loop (think → tool call → observe → repeat) | `agent/agent.py` (`create_react_agent(...)`) | ✅ |
| 5 | Short-term memory | LLM context window across the messages of one turn | implicit (LangGraph passes the full message list each step) | ✅ |
| 6 | Session memory | LangGraph `MemorySaver` checkpointer, keyed by `thread_id` from the frontend's `st.session_state` | `agent/agent.py` (`checkpointer=MemorySaver()`); `frontend/app.py` (per-session `thread_id`) | ✅ in-process (deliberately; v1) |
| 7 | Output formatter | Two layers: (a) backend `renderers.py` infers tables / mermaid / code / text from tool output shape; (b) the LLM is prompted to emit Mermaid for relationships and rely on auto-tables for lists | `agent/renderers.py`; prompt in `agent/prompts/system.md` | ✅ |
| 8 | Guardrails | (a) **Scope refusal** via `BOT_DOMAIN`; (b) **Tool allow/deny list**; (c) **Read-only enforcement** in the MCP server itself (blocks `ALTER`/`DEFINE`/etc.); (d) **Hostname allow-list** so even an exploited agent can't reach prod hosts; (e) **Error sanitisation** so raw tracebacks never reach the user | `agent/agent.py` (SCOPE_BLOCK_TEMPLATE), `agent/mcp_client.py` (`_filter_tools`), `mqacemcpserver/server/safety.py`, `mqacemcpserver/server/errors.py` | ✅ multi-layer |
| 9 | Observability | (a) **Per-call JSONL log** on the MCP server side (`logs/queries-*.jsonl`) with tool, args, endpoints, latency, outcome; (b) **Streaming tool-step events** (`tool_call`, `tool_result`) so the UI shows each step the agent took; (c) **`/api/health`** surfaces resolved prompt source, scope, allow/deny lists | `mqacemcpserver/server/query_log.py`, `agent/app.py` (event stream + `/api/health`), `frontend/renderers.py` (tool-step expander) | ✅ |
| 10 | Streaming I/O | SSE `text/event-stream` of typed events (`token`, `tool_call`, `tool_result`, `final`, `done`); frontend reader assembles incrementally | `agent/app.py` (`_run`, `_sse`), `frontend/client.py` (SSE reader) | ✅ |
| 11 | Multi-agent | **Single agent.** No supervisor / delegation pattern. | — | ❌ by design (see "What we don't do") |
| 12 | Human-in-the-loop | No approval-before-action step. The agent is read-only by construction (every MCP tool is `GET`-only or `runCommand` with the modification verbs blocked at the server), so we don't need it. | — | ❌ replaced by upstream read-only enforcement |

---

## Which agentic pattern this is

There's a small zoo of named agentic patterns in the literature. Naming
ours precisely is useful because it tells reviewers what to expect (and
what *not* to expect) without re-reading the code.

### The pattern: **stateful ReAct, single agent, conversational**

**ReAct** = "**Rea**soning + **Act**ing" (Yao et al., 2022). The LLM
alternates between:

1. **Thought** — internal reasoning: *"to answer this I need the queue
   depth, the right tool is `get_queue_depth`."*
2. **Action** — tool call with structured arguments.
3. **Observation** — the tool's return value gets appended to the
   message history.
4. **Loop** — back to step 1, until the LLM decides it has enough to
   answer (no more tool call → final message).

```
              ┌──────────────────────────────────────────┐
              │                                          │
              ▼                                          │
   user msg ──► [LLM thinks] ──tool call?──yes──► [run tool] ──┐
                    │                                          │
                    │ no (final answer)                        │
                    ▼                                  observation
              stream tokens ──► done                          │
                                                              │
                                  ◄───────────────────────────┘
```

This system also adds a fifth element on top of vanilla ReAct: a
**checkpointer** (`MemorySaver`) that snapshots the message history per
`thread_id`, so the *next* user turn picks up where the previous one
finished. That's why we call it **stateful** ReAct.

### In Anthropic's *Building Effective Agents* taxonomy

Anthropic's reference taxonomy splits LLM systems into two top-level
families:

- **Workflows** — orchestration paths are *predefined in code*. Examples:
  prompt chaining, routing, parallelization, orchestrator-workers,
  evaluator-optimizer.
- **Agents** — orchestration is *LLM-driven*. The model decides which
  step to take next, in what order, and when to stop.

This solution is firmly in the **Agents** family. Within that family,
it's the simplest variant: **a single agent in a tool-use loop with
session memory**. No multi-agent supervisor, no parallel branches, no
reflection critic.

### Specific characteristics (one row = one design decision)

| Dimension | This solution | Why |
|---|---|---|
| Pattern family | **Agent** (not Workflow) | Control flow is LLM-decided. There is no `if intent == "mq" else …` anywhere. |
| Loop variant | **ReAct** | Tool calls and observations are interleaved one at a time, not pre-planned. |
| Agent count | **Single** | One catalog, 14 tools — splitting into MQ-agent + ACE-agent would add coordination overhead with no gain. |
| Statefulness | **Stateful** (session memory via `MemorySaver`) | "Now show its channels" follow-ups need carry-over context. |
| Initiation | **Reactive** (user-driven) | Not autonomous/scheduled. The loop only runs in response to a user turn. |
| Action class | **Read-only / advisory** | Every MCP tool is `GET`-only or modification-blocked, so no approval gate is needed. |
| Termination | **LLM-decided** | The loop ends when the model produces a final answer (no tool call). LangGraph's `recursion_limit` is the safety stop. |
| Concurrency | **Sequential** | One tool call at a time per step. Parallel tool calls are supported by LangGraph but unused here. |
| Streaming | **Token-level** to UI, **event-level** for tool steps | SSE; see wire protocol in the README. |
| Output shape | **Structured rendering hints** (table / mermaid / code / text) | Auto-derived in the backend renderer + LLM-emitted Mermaid for relationships. |

### Where the pattern is wired up (one place to read)

`agent/agent.py:build_agent`:

```python
agent = create_react_agent(
    model=llm,                # the reasoner
    tools=tools,              # the action space
    checkpointer=checkpointer,# what makes it stateful
    prompt=system_prompt,     # scope guardrail + formatting rules + tool catalog
)
```

That's it. `create_react_agent` is LangGraph's prebuilt ReAct loop —
swap it for `create_supervisor_agent` and you'd have the multi-agent
pattern; remove `checkpointer` and you'd have stateless one-shot ReAct.
The rest of the codebase (FastAPI, SSE streaming, renderers, frontend)
is plumbing around this single call.

### Patterns we considered and didn't pick

| Alternative | Would have looked like | Why we passed |
|---|---|---|
| **Routing workflow** | A small classifier LLM picks an "MQ path" or "ACE path"; each path is a hand-written tool sequence. | Loses the model's ability to mix MQ + ACE in a single turn (*"find the QM hosting flow X and show its channel status"*). Re-introduces the dispatcher we're explicitly avoiding. |
| **Plan-and-Execute** | Up-front: "1) find QM, 2) get depth, 3) summarise." Then execute. | Overhead for short Q&A. Plans go stale when the first tool returns something unexpected. ReAct adapts step-by-step naturally. |
| **ReWOO** (Reasoning WithOut Observation) | LLM emits all tool calls in one shot, executes them in parallel, then composes. | Saves tokens but assumes tools are independent. Many of ours have ordering dependencies (alias → target queue → depth). |
| **Multi-agent** (supervisor + MQ worker + ACE worker) | A supervisor LLM routes each turn to a specialised sub-agent. | 2-3× the LLM cost per turn; no behavioural gain because one tool catalog already covers both halves cleanly. |
| **Reflexion / self-critique** | After each answer, a critic LLM evaluates and the agent revises. | Useful when answer correctness is hard to verify. For diagnostic Q&A, the tool output *is* the source of truth — a critic adds latency without changing the answer. |
| **Parallel tool calls within one ReAct step** | LLM emits 3 tool calls in one step; runtime executes them concurrently. | Supported by LangGraph; worth turning on later if we add tools that have no ordering dependencies. Currently sequential. |

### When you'd outgrow this pattern

Signs that the single-agent stateful ReAct shape is no longer the right
fit, and what to upgrade to:

- **Tool catalog grows past ~50** → LLM picks the wrong tool more often.
  Move to **routing-then-ReAct**: a cheap classifier narrows the toolset
  per turn before the ReAct loop sees it.
- **Same multi-step recipe runs over and over** → factor it out as a
  **workflow** (prompt chaining or orchestrator-workers); cheaper and
  more deterministic than re-deriving the plan every time.
- **Answer correctness matters more than latency** → bolt on
  **Reflexion** (critic node before the final reply).
- **Write/destructive actions land** → add **human-in-the-loop**
  approval as an interrupt node before any tool call with side effects.
  LangGraph supports this natively via `interrupt_before`.
- **Need to act without a user turn** (e.g. *"alert me if queue depth
  exceeds X"*) → add a **scheduler / autonomous loop**; the agent is
  no longer purely reactive.

---

## Five concrete behaviours that prove the agentic loop

Each example shows a single user message and what actually happens inside
the system. These are the behaviours that distinguish agentic AI from
"chatbot wrapping an API".

### 1. Multi-step reasoning across tools (no human prompt-chaining)

> **User:** "What's the depth of queue PAYMENTS.IN?"

What the agent does, autonomously:

1. Reads its tool catalog and picks `get_queue_depth` (because the
   docstring matches the user intent).
2. Calls `get_queue_depth(queue_name="PAYMENTS.IN")`.
3. Tool returns the depth across multiple QMs *and* — because the queue
   is a `QALIAS` — the alias-resolution chain.
4. Agent decides the answer is now sufficient and replies with the depth
   table plus a Mermaid diagram of the alias chain (because its system
   prompt told it to draw a diagram for relationship-shaped data).

No code in this repo orchestrates steps 1–4. The LLM chose all of them.
Replace `get_queue_depth` with a different MCP tool and the agent
re-plans without changes anywhere in the codebase.

### 2. Tool selection from natural language alone

> **User:** "Show me the integration servers running on lodaceN1."

The model never sees a routing table. It selects `list_ace_servers` purely
from the tool's description: *"IBM ACE: List integration servers on a
specific integration node."* This is the **agentic** part of "tool use" —
the dispatcher is the model itself, not us. (`agent.py:_format_tool_catalog`
just hands the LLM a bullet list of tool name + first line of docstring.)

### 3. Conversational follow-ups using session memory

Turn 1: *"Run `DISPLAY QMGR` on QM1."* → tool call, response.
Turn 2: *"Now show its channels."* → the model references *"its"* by
recalling QM1 from the prior turn and calls
`runmqsc(qmgr_name="QM1", mqsc_command="DISPLAY CHANNEL(*)")`.

The memory comes from `MemorySaver` keyed by the frontend's
`thread_id`. The Reset button rotates the `thread_id`, after which the
same question would receive a clarifying request — proving memory
actually drove the resolution, not the model guessing.

### 4. Goal-directed refusal (guardrail in action)

> **User:** "What's the weather in Bangalore today?"

With `BOT_DOMAIN=IBM MQ and IBM ACE`, the agent recognises this is out
of scope, **does not call any tool**, and replies with the fixed refusal
sentence. The frontend shows zero tool steps — the proof that the
guardrail intercepted the loop before the action phase.

### 5. Restricted action space (capability boundary)

> **User:** "Delete queue ORDERS.STAGE."

Even if the model wanted to comply, two layers stop it:
1. There is no destructive tool in the registry — the MCP server only
   exposes read-only tools.
2. The MCP server's `runmqsc` blocks modification verbs (`DELETE`,
   `ALTER`, `DEFINE`, …) at the tool layer regardless of who asks.

This is the agentic-AI safety pattern called **constrained capability**:
narrow what the agent *can* do, rather than relying on prompts to keep it
in line.

---

## What we deliberately don't do

Being honest about scope is part of the agentic-AI claim. This system
implements the *core* loop and *common* guardrails. It does **not**
implement:

| Pattern | Why we skip it (v1) |
|---|---|
| **Multi-agent / supervisor patterns** | One agent is enough for diagnostic Q&A. Splitting MQ-agent vs ACE-agent would add coordination overhead with no behavioural gain — both halves' tools are already in one catalog. |
| **Long-term memory / RAG** | The MCP tools *are* the knowledge source (live MQ/ACE state + offline manifests). Adding a vector store would duplicate that. We may add it later for "remember the last 50 incidents I asked about" use cases. |
| **Reflection / self-critique loops** | LangGraph supports adding a critic node, but for read-only diagnostics the cost (extra LLM call per turn) outweighs the benefit. Tool errors surface directly. |
| **Human-in-the-loop approval** | Replaced by upstream constraint: the MCP server itself is read-only, so there's nothing destructive to approve. Add this if/when write-capable tools are introduced. |
| **Prompt-injection regex / output redaction** | Designed but not built (see plan file). Would add ~300ms latency. The MCP server already redacts sensitive kwargs in logs; the bot is read-only so blast radius from a successful injection is limited. |
| **Rate limiting / max-iteration caps** | LangGraph has a default `recursion_limit`; we haven't exposed a tighter env-driven cap yet. Easy to add when needed. |
| **Persistent memory** | `MemorySaver` is in-process. A restart wipes thread state. Swap for `SqliteSaver` / `PostgresSaver` if survivable history is needed. |
| **Autonomous goal initiation** | The agent only acts in response to a user turn. No background loop, no scheduled actions. By design — this is an assistant, not an autonomous operator. |

These are *omissions, not failures*. Most can be added in a few hundred
lines without changing the architecture, because the core ReAct loop +
checkpointer + tool-loader pattern absorbs them cleanly.

---

## Side-by-side: this solution vs. a non-agentic system

To make the claim concrete, contrast against what a **non-agentic** chat
solution would look like for the same use case.

| Capability | Non-agentic version | This system |
|---|---|---|
| Routing user intent to MQ vs ACE | Hand-written if/else on keywords | LLM picks tool from descriptions |
| Choosing the right tool for an MQ question | Switch statement on intent label from a classifier | LLM evaluates 14 tools, picks one |
| Calling multiple tools to answer one question | Pre-defined workflow per intent | LLM calls another tool after observing the first result if needed |
| Carrying context to follow-up questions | Per-intent slot filling | LLM reads prior turns from session memory |
| Adapting to a renamed/added tool | Code change in router | Zero code change — just restart so the new tool catalog reaches the LLM |
| Adapting to a different MCP server | Rewrite of router + intent classifier | Change `MCP_SSE_URL`, restart |
| Handling out-of-scope questions | Per-keyword block list | Single `BOT_DOMAIN` env var; LLM enforces semantically |

The right-hand column is what makes it agentic: every behaviour is the
result of the LLM reasoning over its current state and tool set, not
pre-coded logic.

---

## Glossary (so the conversation stays grounded)

- **Agent**: an LLM running inside an action loop with access to tools
  and memory.
- **Tool**: a callable function the agent can invoke, described to the
  LLM in natural language. Here, every tool is an MCP tool.
- **MCP (Model Context Protocol)**: Anthropic's open protocol for giving
  agents structured access to external systems. Both stdio and SSE
  transports are supported; this stack uses SSE.
- **ReAct**: "Reasoning + Acting" — the prompt pattern where the LLM
  alternates between thoughts and tool calls. `create_react_agent`
  implements this. The dominant agentic pattern; this solution uses it.
- **Stateful ReAct**: ReAct + a checkpointer that persists message
  history per session. What makes follow-up questions ("now show its
  channels") work without the user repeating context.
- **Workflow vs Agent** (Anthropic taxonomy): a *workflow* has its
  control flow coded explicitly (routing, prompt chaining, etc.); an
  *agent* delegates control flow to the LLM. We're an agent.
- **Checkpointer**: in LangGraph, the component that persists agent
  state between steps and turns. We use `MemorySaver` (in-process dict).
- **Thread / session**: one continuous conversation. Identified by a
  `thread_id` so memory can be scoped per-conversation rather than
  global.
- **Guardrail**: anything that constrains the agent's autonomy
  (allow/deny lists, scope refusal, max iterations, read-only
  enforcement).

---

## References

- Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* — <https://arxiv.org/abs/2210.03629>
- Anthropic, *Building effective agents* (Workflow vs Agent taxonomy) — <https://www.anthropic.com/engineering/building-effective-agents>
- Anthropic, *Model Context Protocol* — <https://modelcontextprotocol.io>
- LangGraph docs, *Prebuilt ReAct agent* — <https://langchain-ai.github.io/langgraph/reference/prebuilt/>
- Lilian Weng, *LLM Powered Autonomous Agents* — <https://lilianweng.github.io/posts/2023-06-23-agent/>
- OpenAI, *A practical guide to building agents* — <https://platform.openai.com/docs/guides/agents>

---

## TL;DR (for the next time someone asks)

> "It's agentic AI because at runtime the LLM is the decision-maker, not
> the code. Every turn it picks which of the 14 IBM MQ / ACE / certificate
> tools to call, in what order, with what arguments, and when to stop — guided
> only by tool descriptions and the conversation so far. The pattern is
> **stateful ReAct, single agent, conversational** — Anthropic's
> 'Agent' category, not 'Workflow'. The code around it is just plumbing:
> the ReAct loop (LangGraph's `create_react_agent`), session memory
> (`MemorySaver`), tool transport (MCP / SSE), guardrails (scope refusal,
> tool allow/deny, read-only enforcement), and structured output. None
> of those things route or decide; only the model does."
