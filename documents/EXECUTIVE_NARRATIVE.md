# Executive Presentation — Story Outline

**Working title:** *Agentic AI for IBM MQ & ACE Diagnostics — One Endpoint, Zero Manual Toil*

**Audience:** mixed executive + IT leadership. Outcome-focused, light on code. 8 slides.

---

## Slide 1 — Title & Framing

- **Title:** Agentic AI Diagnostics Assistant for IBM MQ & IBM ACE
- **Sub-line:** A single conversational interface that replaces console-hopping, ticket queues, and tribal knowledge — built on the Model Context Protocol (MCP).
- **Visual cue:** one screenshot of the chat UI answering *"What's the depth of PAYMENTS.IN?"* with a table + Mermaid alias chain.

---

## Slide 2 — The Problem

**Headline:** *Middleware diagnostics today is manual, console-heavy, and tied to a handful of platform admins.*

**What the platform support admin does every day:**

- Logs into **multiple servers individually** to check queue manager and integration node state — no single pane of glass.
- Opens **multiple `mqweb` consoles** (one per QM host) to inspect queue depths, channel status, and run MQSC commands.
- Opens **multiple ACE web UIs / Toolkit sessions** (one per integration node) to check servers, applications, and message flows.
- **Hops between consoles + CSV inventory dumps + ServiceNow** to answer even simple questions like *"which QM hosts queue X?"* or *"is flow Y deployed on node N?"*.
- **Resolves alias queues by hand** — follow the alias to its target queue, then go check depth on the right QM.
- **Greps offline node dumps** for BIP error codes when a node is unreachable.
- **Repeats the same triage steps** for the same recurring questions from app teams, L1, and L2 — no leverage, no scale.

**Result:**

- High effort on routine diagnostic questions — minutes-to-hours of console navigation per question.
- **Platform admins are the bottleneck** — every app team query escalates to the same small group of MQ/ACE SMEs.
- **No consolidated, queryable view** of the MQ + ACE estate — knowledge lives in admins' heads and scattered consoles.
- **Toil dominates the day** — admins spend cycles answering "what is the state of X?" instead of doing engineering work (capacity, upgrades, automation).
- **Inconsistent answers** — different admins navigate the consoles differently, so the same question can produce slightly different responses.

*Visual:* an "admin's screen" mockup — five browser tabs (mqweb#1, mqweb#2, ACE webUI#1, ACE webUI#2, ServiceNow) + a terminal + an Excel of `qmgr_dump.csv`, all open at once.

---

## Slide 3 — From Manual Toil to Conversational Intelligence

**Headline:** *Replace a chain of consoles, scripts and SMEs with a single conversation.*

**Today** — diagnostics is a multi-console, multi-team, expert-dependent activity. Every routine question travels through a queue of people before it gets an answer.

**With the AI Agent** — the same question is answered in one place, in plain English, by an autonomous AI agent that reasons across the entire MQ + ACE estate on the user's behalf.

**The shift, in three executive takeaways:**

- **From many screens to one conversation.** A unified chat experience replaces the patchwork of mqweb consoles, ACE web UIs, inventory dumps and ticket queues.
- **From expert-gated to self-service.** App teams, L1, and L2 get instant, consistent answers without escalating to platform SMEs — admins are freed for higher-value engineering work.
- **From tribal knowledge to institutional capability.** What used to live in admins' heads now lives in a governed, auditable, always-on assistant — answers are repeatable, logged, and equally available to everyone.

*Visual:* a clean "before vs after" diagram — left side a tangle of consoles + tickets + SMEs feeding into "Answer (hours)"; right side a single chat box feeding into "Answer (seconds)".

---

## Slide 4 — One-Click Solution & Key Components

**Headline:** *Press one button. Three things start, one unified experience.*

`scripts\start-all.ps1` launches the entire stack. Components:

1. **Unified MCP Server** (Python) — 13 read-only tools (7 MQ + 6 ACE) behind one endpoint.
2. **Agentic Backend** (FastAPI + LangGraph + GPT-4o) — the reasoning loop.
3. **Web Chat UI** (Next.js + Tailwind) — operator interface with streaming tool steps, tables, Mermaid diagrams, session memory.

*Visual:* a 3-box architecture diagram — Browser → Agent Backend → MCP Server → (MQ REST, ACE Admin REST, CSV manifests).

**Why MCP matters (1 line for execs):** MCP is Anthropic's open protocol — the **USB-C of AI tools**. Any compliant LLM client (Claude Desktop, VS Code Copilot, Cursor, our chat UI) plugs into the same server without rewrite.

---

## Slide 5 — What Makes This "Agentic" (not just a chatbot)

**Headline:** *The LLM decides — no hand-coded if/then routing anywhere.*

Mapped to the canonical agentic-AI checklist (10 of 12 components implemented):

| Capability | How it shows up |
|---|---|
| **LLM reasoner** | GPT-4o picks the next step every turn |
| **Tool registry** | 13 MQ + ACE tools auto-loaded over MCP |
| **Tool selector** | The LLM itself — guided by tool docstrings, zero dispatcher code |
| **ReAct action loop** | Think → call tool → observe → repeat, until done |
| **Session memory** | "Now show *its* channels" works — `MemorySaver` keyed by `thread_id` |
| **Structured output** | Auto-renders tables, Mermaid diagrams, code blocks |
| **Streaming I/O** | UI shows every tool step as it happens |
| **Observability** | Per-call JSONL log — feeds Power BI dashboards |

**Pattern in one line:** *Stateful ReAct, single agent, conversational* (Anthropic's "Agent" category, not "Workflow").

*Visual:* the ReAct loop diagram from `AGENTIC_AI.md`.

---

## Slide 6 — Security Guardrails (Defense in Depth)

**Headline:** *Bounded autonomy — six independent layers.*

1. **Read-only by construction** — every MQ/ACE tool is `GET`-only or `runmqsc` with `ALTER / DEFINE / DELETE / PURGE / STOP / …` blocked at the tool layer. No destructive tool exists in the registry.
2. **Hostname allow-list** — outbound calls resolve the target and reject anything not matching `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES`. Production hosts excluded by default.
3. **Scope refusal** — `BOT_DOMAIN` makes the agent refuse off-topic questions (weather, code review, etc.) *without* invoking any tool.
4. **Tool allow/deny list** — env-driven; lets ops disable a specific tool without code changes.
5. **Secure endpoints** — the MCP server's SSE endpoint is gated by HTTP Basic Auth (`MCP_AUTH_USER` / `MCP_AUTH_PASSWORD`), with an unauthenticated `GET /healthz` carved out only for liveness probes; a startup warning fires if auth is disabled. The endpoint is ready to sit behind TLS termination / a reverse proxy / an API gateway in production.
6. **Error sanitisation + redacted logs** — users see short, ref-tagged messages; full tracebacks and secret-looking kwargs (`password`, `token`, `key`, …) are kept out of the user path and redacted in logs.

**Audit-ready posture:** every call is logged with `request_id`, caller, tool, args (redacted), endpoints hit, latency, outcome — directly ingestible into Power BI.

---

## Slide 7 — ROI & Business Value

**Headline:** *Hard savings + soft wins, all measurable.*

**Hard ROI levers**

- **SME deflection** — L1/L2 (and even app teams) now answer their own diagnostic questions; MQ/ACE Infra reclaims time for engineering work.
- **Ticket-volume drop** — the chatbot answers the "what's the depth?" / "is the flow deployed?" class of tickets that dominate infra queues.
- **Onboarding speed** — new joiners learn MQ/ACE by *asking* instead of shadowing.
- **Productivity multiplier on the admin team** — one assistant absorbs the recurring, low-complexity queries that today consume the majority of admin attention.

**Soft ROI**

- Consistent, auditable answers (no SME variability).
- Power BI dashboards on the query log surface trends infra never had before (top queues queried, error hotspots, latency outliers).
- A defensible, governed AI footprint — read-only by construction, allow-listed, logged — that satisfies risk & audit before broader rollout.

*Visual:* a simple "before vs after" bar chart — ticket volume to infra, % tickets needing SME escalation, admin hours spent on routine queries.

*(Suggested: insert your client's actual baseline numbers here once you have them — placeholder for now.)*

---

## Slide 8 — Reusability Across Clients

**Headline:** *Built once, deployable everywhere — ~75% of the solution is reusable framework.*

The architecture cleanly separates **the agentic chassis** (reusable as-is) from **the domain layer** (swapped per client).

### Reusable across every client (~70–80%)

| Layer | What gets reused |
|---|---|
| **Web Chat UI** (Next.js + Tailwind) | 100% — streaming, tables, Mermaid, session memory, reset, branding hooks |
| **Agentic Backend** (FastAPI + LangGraph ReAct + MemorySaver) | 100% — reasoning loop, tool loader, SSE event stream, output renderers |
| **MCP Server scaffold** (config, logging, query log, error sanitiser, auth middleware, safety primitives) | 100% — drop in new tools and you inherit logging, auth, allow-listing, observability for free |
| **Guardrail framework** (scope refusal, tool allow/deny, hostname allow-list, read-only enforcement, secret redaction) | 100% — policy values change; the enforcement code does not |
| **Observability stack** (JSONL query log → Power BI ingestion pattern) | 100% — same dashboards work for any tool set |
| **Test scaffold** | ~100% — safety, query-log, error-sanitiser tests carry over; only domain-tool tests are new |

### Different per client (~20–30%)

| Layer | What changes |
|---|---|
| **Domain tools** (MQ helpers + ACE helpers + `@mcp.tool` wrappers) | Replaced with the client's target systems — e.g. ServiceNow, Kafka, Kubernetes, Splunk, mainframe tooling |
| **System prompt** (`prompts/system.md`) | Rewritten for the new domain's terminology, scope rules, escalation team |
| **Bot domain & scope** (`BOT_DOMAIN`) | One env line per client |
| **Allow-lists** (hostnames, tools) | Per-client policy values in `.env` |
| **Inventory manifests** (CSV dumps) | Client-specific extracts; the loader is reusable |
| **MCP endpoint URL** | Single config line — `MCP_SSE_URL` — repoints the whole stack |

### What this means commercially

- **A new client engagement is a tool-build, not a platform-build.** ~75% of the code ships on day one; the remaining 25% is the client-specific domain layer where the actual value lives.
- **Multi-tenant deployment** is feasible: one shared codebase, per-client `.env` + allow-lists, per-client tool sets.
- **Same MCP server is consumable by any MCP-compliant client** (Claude Desktop, VS Code Copilot, Cursor, our chat UI) — no per-client integration work on the consumer side.
