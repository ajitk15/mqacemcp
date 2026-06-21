# PPT Narrative Outline
## "From Console Chaos to Conversational Intelligence"
### Agentic AI Diagnostics for IBM MQ & IBM ACE — 10-Minute Presentation

**Audience:** Mixed executive + IT leadership
**Tone:** Problem-forward, outcome-focused, light on code
**Total slides:** 10 | **Target runtime:** 10 minutes

> **How to use this document:** Each slide section contains (1) the spoken narrative arc, (2) headline/sub-headline copy, (3) exact visual/infographic prescription, and (4) key talking points. No colour coding is specified — the designer decides palette. Visual type is prescribed in detail so any designer can execute it without ambiguity.

---

---

## SLIDE 1 — TITLE SLIDE
**Timing: ~30 seconds**

### Headline Copy
> **Agentic AI Diagnostics Assistant**
> for IBM MQ & IBM ACE
>
> *One conversation. Zero console-hopping. Always-on.*

### Visual Prescription
**Type: Minimal typographic layout — full bleed, no background imagery**

- Large centred title in two lines — "Agentic AI Diagnostics Assistant" on line 1, "for IBM MQ & IBM ACE" on line 2 in a lighter weight
- Sub-line below in italic, smaller: *One conversation. Zero console-hopping. Always-on.*
- Bottom-left corner: presenter name, role, date — in small caps or monospace font
- Bottom-right corner: a single small chat-bubble icon or terminal `>_` glyph — the only graphic element on the slide, suggesting "conversation"
- Generous white space dominates the layout; no charts, no logos, no gradients

### Spoken Narrative
"Today I want to show you how a single conversational AI agent can replace the patchwork of consoles, tickets, and tribal knowledge that our MQ and ACE platform support team navigates every single day. By the end of ten minutes, you'll have seen it answer a real question — live — and understand exactly how it does it safely."

---

---

## SLIDE 2 — WHAT ARE MQ & ACE, AND WHERE DO THEY FIT?
**Timing: ~1 minute**

### Headline Copy
> **The invisible backbone of enterprise integration**
> *IBM MQ guarantees the message. IBM ACE transforms it.*

### Visual Prescription
**Type: Layered enterprise architecture diagram (vertical stack, 7 layers)**

Draw a clean vertical stack of seven labelled bands. Left edge of each band has a short descriptor; right edge has a one-line role summary. Highlight two bands distinctly (different shade or bold border) — those are the MQ and ACE layers.

| Layer (top to bottom) | Label on slide | Highlight? |
|---|---|---|
| 1 | Channel / Experience — member, broker, provider portals, mobile | — |
| 2 | Experience APIs / API Gateway — OAuth, throttling, rate limits | — |
| 3 | Process / Orchestration — BPM, case management, workflow | — |
| **4** | **Integration — IBM ACE** — message flows, mapping, mediation, routing | **YES — ACE** |
| **5** | **Messaging / Transport — IBM MQ** — durable queues, topics, HA cluster | **YES — MQ** |
| 6 | Systems of Record — Claims, Policy Admin, Member, Provider, Mainframe | — |
| 7 | Data Layer — Data warehouse, lake, MDM | — |

**Connector arrows:** A downward-flowing arrow on the left side of the stack labelled *"Business request flows down"*; an upward arrow on the right labelled *"Response flows up"*.

**Two callout boxes** floating beside the highlighted layers:
- ACE callout: *"Transforms protocol & format — JSON → X12, REST → CICS, HTTP → MQ"*
- MQ callout: *"Guarantees delivery — once and exactly once, even if downstream is down"*

**Do NOT use a busy network diagram here** — this is purely the conceptual layer model. The network view comes on the next slide.

### Key Talking Points
- MQ is the enterprise's messaging transport — it holds a message durably in a queue until the downstream system is ready, even if that system is restarting or doing nightly batch processing. Nothing is lost.
- ACE sits one layer above as the integration runtime — it is where "an HTTPS JSON request from a partner portal" turns into "an X12 EDI claim on an MQ queue", without any application code rewrite.
- Together they form the ESB (Enterprise Service Bus) at the heart of the carrier's IT estate. Every claims intake, eligibility check, enrollment feed, and provider data exchange passes through this layer.
- The key insight: **MQ owns the transport guarantee. ACE owns the translation.** Change one side without touching the other.

---

---

## SLIDE 3 — HOW INSURANCE / MEDICARE USES THESE PLATFORMS
**Timing: ~1 minute**

### Headline Copy
> **Every Medicare transaction you touch passes through this bus**
> *Claims. Eligibility. Enrollment. Pharmacy. Correspondence. All of it.*

### Visual Prescription
**Type: Hub-and-spoke network diagram with labelled flows — two tiers**

Draw the diagram in two clear horizontal tiers with the ESB bus (MQ + ACE) as the centre band running across the middle of the slide.

**LEFT SIDE — Inbound channels (feed into ACE/MQ):**
Draw 5–6 node boxes stacked vertically on the left, each with a small icon and label, with an arrow pointing right into the ESB band:
- Member / Broker / Provider Portals → arrow labelled "REST/JSON, SOAP"
- EDI Gateway / Clearinghouses → arrow labelled "X12 837, 270, 834"
- Partner APIs (PBM, Reinsurance, TPA) → arrow labelled "HTTP, AS2, SFTP"
- Batch File Drops → arrow labelled "File/FTP"

**CENTRE BAND — The ESB (two boxes side by side inside the band):**
- Left box: IBM ACE — "Normalise, transform, route"
- Right box: IBM MQ — "Queue & deliver, guaranteed"
- A double-headed arrow between them

**RIGHT SIDE — Systems of record (receive from MQ/ACE):**
Draw 6 node boxes stacked vertically on the right, each with a small icon and label, with an arrow pointing right from the ESB band:
- Claims Adjudication → labelled "X12 835 / 277CA out"
- Policy Admin System
- Member Master
- Provider Master
- Mainframe (CICS/IMS) → labelled "via CICS transaction"
- Data Warehouse / Lake

**Below the diagram — a small 2×3 table of key Medicare workload flows:**
| Workload | Standard | Why MQ + ACE |
|---|---|---|
| Claims intake | X12 837 in / 835 out | Transactional, never lose a claim |
| Eligibility | X12 270/271 | Sub-second, spike-tolerant |
| Enrollment | X12 834 | Batch delta, multi-source |
| Member portals | REST→CICS | Decouples portal from mainframe |
| Pharmacy / PBM | NCPDP | Strict latency + reliability |
| Correspondence | EOB/print | Fan-out via MQ pub/sub topics |

### Key Talking Points
- Every eligibility check a member submits at the pharmacy counter travels through this bus in under a second. MQ absorbs the spike; ACE translates between the web-facing REST API and the CICS transaction on the mainframe.
- Every claim submitted by a provider clearinghouse is transformed by ACE from X12 837 EDI into what the adjudication engine expects — and MQ ensures the claim sits safely in a queue if the adjudication engine is in a nightly batch window.
- The bus decouples everything. Upgrade the member portal, the mainframe stays untouched. Add a new partner feed, the core systems don't care.

---

---

## SLIDE 4 — HOW BIG IS THE PLATFORM WE SUPPORT?
**Timing: ~45 seconds**

### Headline Copy
> **This is not a small integration layer.**
> *It is a mission-critical, multi-site, always-on infrastructure.*

### Visual Prescription
**Type: Infrastructure topology "map" — a combination of stat callouts and a simplified topology sketch**

**TOP HALF — Large-number stat callouts (4–5 "data bricks" in a row):**
Each brick is a bold number with a short label beneath it. Use the actual numbers from your environment; placeholders shown:

| Stat | Example placeholder |
|---|---|
| Queue Managers | **18 QMs** across 4 environments |
| ACE Nodes | **12 Integration Nodes** |
| Integration Servers | **40+ servers** |
| Deployed Applications | **120+ app deployments** |
| Active Queues | **1,400+ queues** |
| Message Flows | **500+ flows** |

*(Replace with actual figures from your estate inventory)*

**BOTTOM HALF — Simplified topology sketch showing the three major topology patterns side by side:**

Draw three small topology boxes labelled A, B, C:

**Box A — Single QM, single ACE node (simple)**
```
[ACE Node]──[QM1]──[App1]
```
Used for: dev/test, isolated workloads

**Box B — HA Cluster (multi-QM, shared queue)**
```
[ACE]──[QM1 (active)]
       [QM2 (standby)]──[App cluster]
       [QM3 (standby)]
```
Used for: production claims, eligibility

**Box C — Multi-site federation (channels between QMs)**
```
[Site A: QM-PROD1]──channel──[Site B: QM-PROD2]
       │                            │
   [ACE-A]                      [ACE-B]
```
Used for: DR, partner connectivity, mainframe integration

**Key annotation beneath the topology sketch:**
> Environments: Development · Integration Test · UAT · Production
> Spread across: On-prem Linux · AIX · z/OS (mainframe)

### Key Talking Points
- We are not talking about a sandbox. This is 18+ queue managers, over a thousand queues, hundreds of message flows, spanning on-prem Linux, AIX, and z/OS.
- Three topology patterns run in parallel — simple isolated deployments for dev, HA clusters for production claims processing, and federated multi-site topologies for DR and partner connectivity.
- The operational complexity of just *knowing what is running where* — without any tooling — is enormous. That complexity is the setup for the next conversation.

---

---

## SLIDE 5 — THE DAILY REALITY: "CONSOLE ARCHAEOLOGY"
**Timing: ~1.5 minutes**

### Headline Copy
> **Answering one question takes five open tabs, a terminal, and an expert.**
> *This is what platform support looks like today.*

### Visual Prescription
**Type: "Chaos desktop" mockup — the most important visual in the deck**

Create a realistic-looking desktop screenshot mockup (illustrated, not a real screenshot). Show a laptop/monitor frame containing:

- **5 browser tabs** visible at the top, labelled:
  - `mqweb :: MQPROD1` 
  - `mqweb :: MQPROD2`
  - `ACE WebUI :: NODE-A`
  - `ACE WebUI :: NODE-B`
  - `ServiceNow :: INC0034821`
- **1 terminal window** in the lower-left, showing a partial `dspmq` command output
- **1 Excel spreadsheet** in the lower-right, labelled `qmgr_dump_v3_FINAL.csv`
- **1 chat window** (Teams/Slack) partially visible, showing a message: *"Hey, can you check why APP1 isn't getting messages?"*

**Overlay on top of this mockup — a single bold annotation in a callout box:**
> *"This is the admin's screen for a routine diagnostic question."*

**Below the mockup — a horizontal process strip (swim-lane style) showing the manual steps:**
Draw a left-to-right flow of 6 steps as labelled boxes connected by arrows:

```
[1. Receive ticket / Slack message]
  → [2. Log into mqweb on PROD1 — check queue depth]
  → [3. Log into mqweb on PROD2 — check same queue]
  → [4. Open ACE WebUI — check flow status]
  → [5. Cross-reference qmgr_dump.csv — find alias target]
  → [6. Reply with answer — 20–45 minutes later]
```

**Time annotation** on step 6: a clock icon with label *"20–45 min for a routine question"*

### Key Talking Points
- Every routine diagnostic question — "is this queue backing up?", "is that flow running?", "which queue manager hosts this object?" — requires the admin to open multiple browser tabs, one per queue manager host or ACE node, because there is no single pane of glass.
- There is no consolidated, queryable view of the MQ + ACE estate. The inventory lives in a manually-maintained CSV file. Queue depths are checked one console at a time.
- If the queue has an alias, the admin has to manually resolve that alias — find which queue it points to, then go check the depth on the right queue manager. That's multiple manual hops for a single data point.
- The answer to a question that should take five seconds takes 20 to 45 minutes — not because the admin is slow, but because the tooling forces this workflow.

---

---

## SLIDE 6 — THE CASCADING CHALLENGES
**Timing: ~1 minute**

### Headline Copy
> **Manual toil isn't just slow — it creates four compounding risks.**

### Visual Prescription
**Type: 2×2 challenge grid — four quadrants, each with a title, icon, and 2–3 bullet lines**

Draw a clean 2×2 grid. Each quadrant has a bold title at the top, a simple line-art icon, and 2–3 short impact statements. No colour coding needed — use varying visual weight (bold vs regular) to differentiate titles from body.

**Quadrant 1 (Top-Left): Availability Constraint**
Icon suggestion: a clock face showing 16 hours
- Platform support operates **16×5** — nights and weekends have no coverage
- App teams and L1/L2 cannot self-serve outside business hours
- A queue backup at 9 PM on Friday waits until Monday morning

**Quadrant 2 (Top-Right): Expert Bottleneck**
Icon suggestion: single person surrounded by many arrows pointing at them
- Every diagnostic question escalates to the same small group of MQ/ACE SMEs
- SMEs spend the majority of their day answering "what is the state of X?" questions
- New team members cannot independently diagnose — they shadow experts for months
- Inconsistent answers: different admins navigate differently → different conclusions

**Quadrant 3 (Bottom-Left): Complexity at Scale**
Icon suggestion: branching tree / network graph getting exponentially larger
- 18+ queue managers × multiple environments = dozens of consoles to check
- Alias queue chains require manual multi-hop resolution
- z/OS (mainframe) MQ has a different MQSC dialect — specialist knowledge required
- ACE node + server + application + message flow hierarchy = 4 levels deep per inquiry

**Quadrant 4 (Bottom-Right): Toil Crowding Out Engineering**
Icon suggestion: balance scale — "toil" heavily outweighing "engineering"
- Routine diagnostics consume time that should go to upgrades, automation, and capacity planning
- Repeat tickets: same questions, same apps, same queues — asked daily
- No leverage: no matter how many times a question is answered, the next occurrence takes the same effort
- Knowledge lives in heads, not in systems — creates key-person dependency risk

**Below the grid — one-line bridge sentence to the next slide:**
> *"What if every one of these four problems had the same solution?"*

### Key Talking Points
- The 16×5 coverage window means roughly a third of every week has zero diagnostic capability. That's not a staffing problem — it's an architectural one. You can't staff 24×7 support for a niche skill set economically.
- The expert bottleneck compounds the availability problem. When the two or three people who hold this knowledge are heads-down on an incident, nothing else gets answered.
- The complexity problem is not going away — if anything, as the platform grows, the number of consoles grows linearly. Manual approaches don't scale.
- And the toil problem is the most insidious: it's invisible in capacity planning until suddenly the admin team has no time left for proactive engineering work.

---

---

## SLIDE 7 — THE SOLUTION: AN AGENTIC AI SUPPORT BOT
**Timing: ~1 minute**

### Headline Copy
> **One conversation replaces all of that.**
> *A self-service AI agent that reasons across the entire MQ + ACE estate — securely.*

### Visual Prescription
**Type: "Before vs After" split-slide diagram — left half vs right half**

**LEFT HALF — labelled "Before" (muted, slightly cluttered visual language):**
Draw a simplified version of the chaos desktop from Slide 5:
- Stack of 5 overlapping browser tab icons
- A ticket queue funnel icon with "Wait for SME" label
- A clock showing "20–45 min"
- A person icon at the bottom labelled "Platform Admin (16×5)"

**RIGHT HALF — labelled "After" (clean, open visual language):**
- A single chat interface box — just a text input field and a response area
- Above it: label "Any user — app team, L1, L2, admin"
- The chat box shows one line of input: *"What is the depth of QL.IN.APP1?"*
- Below the chat box: a clock showing "< 10 seconds"
- Below the clock: label "24×7 capable — not gated by staffing"

**Between the two halves — a rightward arrow, wide and bold, labelled:**
> "Agentic AI Support Bot"
> *Built on Model Context Protocol (MCP)*

**Below the split — a horizontal strip with three "shift" statements in three columns:**

| From | → | To |
|---|---|---|
| Many consoles, one question | | One conversation, complete answer |
| Expert-gated access | | Self-service for any authorised user |
| Tribal knowledge in heads | | Institutional, auditable capability |

### Key Talking Points
- The agent doesn't use a dashboard. It doesn't have a separate portal. It's a chat interface — the same interaction model every user already knows.
- Any authorised user — app team member, L1, L2, or the admin themselves — can ask a question in plain English and get a complete, accurate answer, without escalating to a specialist.
- The knowledge that used to live in the admins' heads now lives in the agent — and critically, every answer is logged, auditable, and consistent. Two users asking the same question get the same answer.
- The agent is capable 24×7. Nights, weekends, holidays. No on-call rotation required for routine diagnostics.

---

---

## SLIDE 8 — HOW THE AGENT WORKS (AND WHY IT'S SECURE)
**Timing: ~1 minute**

### Visual Prescription — TWO visual panels on one slide

---

**PANEL A (Left 60% of slide): Architecture flow diagram — 4 layers, left to right**

Draw four labelled boxes connected by rightward arrows:

```
[User Chat UI]  →  [Agentic Backend]  →  [MCP Server]  →  [MQ & ACE APIs]
(Streamlit)        (FastAPI +             (14 read-only      (MQ REST API,
                    LangGraph               tools, Python)     ACE Admin REST,
                    ReAct loop,                               CSV inventories)
                    GPT-5.5)
```

**Below each box — a one-line descriptor:**
- Chat UI: "Streaming chat, tables, diagrams, session memory"
- Agentic Backend: "LLM picks the next tool — no hard-coded routing"
- MCP Server: "14 tools: 7 MQ + 6 ACE + 1 Certificate — all read-only"
- MQ & ACE APIs: "Live data from platform REST endpoints"

**Small label between Chat UI and Agentic Backend:** "SSE stream (real-time)"
**Small label between Agentic Backend and MCP Server:** "Model Context Protocol"

---

**PANEL B (Right 40% of slide): Security shield diagram — 6 concentric rings or stacked layers**

Draw a shield or layered "defence-in-depth" visual. Each layer is labelled. Outermost to innermost:

1. **HTTP Basic Auth** — SSE endpoint requires credentials; unauthenticated access rejected
2. **Hostname Allow-list** — agent can only query pre-approved hosts; production excluded by default
3. **Read-only Enforcement** — ALTER, DEFINE, DELETE, STOP, PURGE are blocked at the tool layer; no destructive tool exists in the registry
4. **Scope Refusal** — agent refuses off-topic questions (weather, code, finance) without invoking any tool
5. **Tool Allow/Deny List** — ops can disable any individual tool without code changes, via config
6. **Audit Log** — every call logged with request ID, caller, tool, args (secrets redacted), latency, outcome → Power BI ingestible

**Label above the shield:** "Bounded Autonomy — 6 Independent Security Layers"

### Key Talking Points
- The agent is architecturally read-only. This is not a configuration option — there are no destructive tools in its registry. It physically cannot ALTER, DELETE, STOP, or PURGE anything. It can only ask questions of the platform.
- The hostname allow-list means the agent cannot reach hosts that haven't been explicitly approved. Production systems can be excluded entirely from the allow-list during rollout.
- Every single call is logged — who asked, what tool was called, what arguments were passed (with secrets redacted), which endpoints were hit, how long it took, and what came back. This is audit-ready posture from day one.
- The scope refusal layer means the agent will not go off-topic. Ask it about the weather and it refuses, without touching any MQ or ACE system at all.

---

---

## SLIDE 9 — LIVE WALKTHROUGH: "WHAT IS THE DEPTH OF QL.IN.APP1?"
**Timing: ~2 minutes — the centrepiece of the narrative**

### Headline Copy
> **Watch the agent think.**
> *A five-step autonomous reasoning loop — from plain English question to verified answer.*

### Visual Prescription
**Type: Vertical step-by-step chat + reasoning strip — the most detailed visual in the deck**

Design this as a split layout: **left column = chat UI mockup** (showing the user's question and the agent's response building up); **right column = "agent thinking" strip** (showing each internal step as a labelled card with the tool name and intermediate output).

---

**CHAT UI (Left column) — static mockup:**

```
┌─────────────────────────────────────────────┐
│  MQ & ACE Support Bot               [reset] │
├─────────────────────────────────────────────┤
│                                             │
│  You:                                       │
│  What is the current depth of               │
│  QL.IN.APP1?                                │
│                                             │
│  Bot: [streaming...]                        │
│  ▶ Searching for QL.IN.APP1 in inventory…   │
│  ▶ Resolving alias chain…                   │
│  ▶ Querying queue depth on MQPROD1…         │
│  ▶ Querying queue depth on MQPROD2…         │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │ Queue        │ QM       │ Depth      │   │
│  │ QL.CLAIMS.IN │ MQPROD1  │  342 msgs  │   │
│  │ QL.CLAIMS.IN │ MQPROD2  │    0 msgs  │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  QL.IN.APP1 is an alias for QL.CLAIMS.IN.  │
│  MQPROD1 has 342 messages queued.           │
│  MQPROD2 is clear (standby QM).             │
│  Both systems accessible and healthy.       │
│                                             │
└─────────────────────────────────────────────┘
```

---

**AGENT REASONING STRIP (Right column) — 5 labelled step cards, stacked vertically with connecting arrows:**

**Step 1 — Understand the question**
Tool: *(no tool called)*
> LLM parses intent: user wants current message depth for queue named QL.IN.APP1. Plans first action: search the inventory manifest.

**Step 2 — Locate the object**
Tool: `find_mq_object("QL.IN.APP1")`
> CSV manifest search returns: QM=MQPROD1, Host=mqprod1.corp, Type=QALIAS. Also found on MQPROD2. Both hosts are on the allow-list. ✅

**Step 3 — Resolve the alias**
Tool: `get_queue_depth("QL.IN.APP1")`  *(alias path)*
> Agent detects object type = QALIAS. Calls `DISPLAY QALIAS QL.IN.APP1` on MQPROD1 — returns TARGET(QL.CLAIMS.IN). Now knows the real local queue to check.

**Step 4 — Get actual depths**
Tool: `get_queue_depth` *(continues — local queue path)*
> Calls `DISPLAY QLOCAL QL.CLAIMS.IN CURDEPTH` on MQPROD1 → **342 messages**
> Same call on MQPROD2 → **0 messages** (standby)

**Step 5 — Compose answer**
Tool: *(no tool called)*
> LLM synthesises: alias chain explained, depth per QM in table, health summary. Renders as structured table + plain-English conclusion. Logs call to JSONL audit record.

---

**Below both columns — a single callout box:**
> *"No code was written. No console was opened. No expert was paged. The agent reasoned through a 4-step tool chain autonomously, in under 10 seconds."*

### Key Talking Points
- Notice what did not happen: no one logged into a browser console. No one opened a CSV file. No one knew off the top of their head that QL.IN.APP1 is actually an alias for QL.CLAIMS.IN — the agent figured that out automatically by following the alias chain.
- The agent streamed its thinking in real time. The user saw each tool call as it happened — not a black box.
- The response is a structured table — QM name, queue name, depth — not a raw MQSC dump. The agent formatted the output for a human, not for a terminal.
- The entire interaction — question, tool calls, endpoints hit, response — is logged with a request ID. Auditable, reproducible, consistent.
- This is not a chatbot that looks up FAQs. This is an autonomous reasoning agent that decided which tools to call, in what order, with what arguments, and composed a human-readable synthesis.

---

---

## SLIDE 10 — THE TRANSFORMATION & WHAT'S NEXT
**Timing: ~1 minute**

### Headline Copy
> **This is not a proof of concept. It is running today.**
> *Three asks — and three things you get in return.*

### Visual Prescription
**Type: Two-column layout — left column "What changes", right column "What you get"**

**LEFT COLUMN — "What changes" (3 rows, each with a before/after pair):**

Draw three rows with a left item (current state) and a rightward arrow and a right item (future state):

| Current | → | Future |
|---|---|---|
| 5+ browser consoles for one answer | | 1 chat message — answered in seconds |
| 16×5 admin availability | | 24×7 AI-assisted self-service |
| Knowledge in SME heads | | Institutional, auditable, always-on |

**RIGHT COLUMN — "Three asks" as three labelled action cards, stacked:**

**Ask 1 — Expand the allow-list**
> Onboard the remaining production QM hosts to the allow-list so the agent can cover the full estate, not just non-prod.

**Ask 2 — Connect L1 / app teams**
> Grant access to app team leads and L1 support staff. Measure ticket deflection after 30 days.

**Ask 3 — Plug in Power BI**
> Route the JSONL audit log to the existing Power BI workspace. Get trend dashboards on top queues, error hotspots, and query volume — visibility you don't have today.

**Bottom of slide — one-line closing statement in large, clean type:**
> *"Every MQ and ACE question your team answers today, the agent can answer tomorrow."*

### Key Talking Points
- Everything shown today is built and running. This is not a roadmap slide. The agent is live, connected to the MCP server, with 14 tools covering the full MQ, ACE, and certificate diagnostic surface.
- The three asks are not large investments — they are configuration changes, access grants, and a log-wiring exercise. No new infrastructure, no new platform.
- The reuse story matters too: about 75% of what's been built — the agentic backend, the chat UI, the security framework, the observability stack — is reusable. The next domain (Kafka, Kubernetes, ServiceNow) replaces only the 25% that is MQ/ACE-specific. The chassis ships on day one.
- We have a clear, measurable 30-day experiment: connect L1 and the app teams, measure how many tickets reach the admin team. We expect a material drop in the routine-diagnostic category that dominates the queue today.

---

---

## APPENDIX: VISUAL SUMMARY TABLE

| Slide | Visual Type | Key Element |
|---|---|---|
| 1 — Title | Typographic full-bleed | Chat bubble / terminal glyph only |
| 2 — MQ & ACE intro | Layered architecture stack (7 bands) | ACE + MQ layers highlighted |
| 3 — Insurance use cases | Hub-and-spoke network diagram + flow table | ESB centre band, left/right nodes |
| 4 — Platform scale | Stat data-bricks + 3-topology mini-sketches | Hard numbers front and centre |
| 5 — Daily reality | "Chaos desktop" mockup + 6-step process strip | 5 browser tabs, Excel, terminal, Teams |
| 6 — Challenges | 2×2 challenge grid | 4 quadrants: Availability, Expert bottleneck, Complexity, Toil |
| 7 — Solution | Before/After split + 3 "shift" statements | Single chat box vs multi-console chaos |
| 8 — How it works | 4-box architecture flow + 6-layer security shield | Read-only enforcement, audit log |
| 9 — Walkthrough | Chat UI mockup + 5-step agent reasoning strip | Alias resolution, depth table, JSONL log |
| 10 — What's next | Before/after table + 3 action cards | Expand, connect, plug in |

---

## APPENDIX: SPEAKER TIMING GUIDE

| Slide | Target Time | Cumulative |
|---|---|---|
| 1 — Title | 0:30 | 0:30 |
| 2 — MQ & ACE | 1:00 | 1:30 |
| 3 — Insurance use cases | 1:00 | 2:30 |
| 4 — Platform scale | 0:45 | 3:15 |
| 5 — Daily reality | 1:30 | 4:45 |
| 6 — Challenges | 1:00 | 5:45 |
| 7 — Solution | 1:00 | 6:45 |
| 8 — Architecture + security | 1:00 | 7:45 |
| 9 — Live walkthrough | 2:00 | 9:45 |
| 10 — What's next | 1:00 | 10:45 |

*Buffer: ~45 seconds built in for transitions and natural pauses.*

---

## APPENDIX: INFOGRAPHIC CHEAT SHEET FOR THE DESIGNER

**Slide 2 — Enterprise Stack:**
Draw 7 horizontal bands in a vertical stack. Use two different visual treatments for the ACE (band 4) and MQ (band 5) rows — heavier border, bolder label weight, or a subtle background treatment. Floating callout boxes for the two highlighted layers. Arrows on left and right edges for flow direction. Keep label text short — one line per band.

**Slide 3 — Insurance Network View:**
Left cluster of 4–5 inbound channel nodes → centre ESB band (two boxes: ACE + MQ) → right cluster of 6 system-of-record nodes. All arrows are directional. Label each arrow with the protocol (REST, X12, SFTP, etc.). The centre ESB band should be visually wider and more prominent than the left/right clusters.

**Slide 4 — Scale Stats:**
Top half: 5–6 "data brick" tiles in a horizontal row. Each tile: large bold number (top), short label (bottom). Leave generous padding inside each tile. Bottom half: three small topology sketches, each in a labelled box. Use simple node-and-line diagrams — no icons needed.

**Slide 5 — Chaos Desktop:**
This is the most important visual in the deck. Design it as an illustrated laptop screen with multiple overlapping windows visible. The overlapping and clutter is intentional — it communicates the problem more powerfully than any text. The process strip below it should use a linear left-to-right flow of 6 boxes with a clock icon on the last box.

**Slide 6 — 2×2 Grid:**
Clean quadrant grid with equal-sized quadrants. Bold title at top of each, simple line-art icon beneath the title, then 2–3 short bullet lines. No bullet points needed on the visual itself — the talking points carry the detail. Grid lines should be visible but not heavy.

**Slide 8 — Security Shield:**
The shield can be drawn as either concentric rings (innermost = most restrictive) or a stacked layer diagram (like a shield cross-section). Six layers, each labelled with its security control name and a one-line descriptor. The innermost layer should be the audit log — it's the control that catches anything the outer layers miss.

**Slide 9 — Walkthrough:**
Two-column layout. Left: a realistic-looking chat UI mockup (can be drawn as a wireframe or a clean flat-design mockup — not a real screenshot). Right: five vertically-stacked step cards, each labelled with a step number, a tool name (in monospace font), and a 2–3 line description of what the agent found/decided. Connecting arrows between the step cards. This is the most information-dense visual — give it the most slide real estate.
