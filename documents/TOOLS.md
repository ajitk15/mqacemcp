# Tool reference — how each MCP tool works (and what it falls back to)

Each tool below has one decision-flow diagram showing the resolution
chain end-to-end: inputs on the left, every branch labelled, and the
outcome at each leaf coloured by category.

> The diagrams use Mermaid — they render inline on GitHub and in any
> Markdown viewer with Mermaid support. The legend just below applies
> to every diagram in this doc.

## Legend

```mermaid
flowchart LR
  IN([input]):::input
  D{decision}:::decision
  HTTP[/HTTP or CSV call/]:::http
  OK[/✅ success/]:::ok
  POLICY[/🚫 policy block/]:::policy
  USER[/❌ user error/]:::user
  ERR[/⚠️ infra error<br/>ref request_id/]:::err

  classDef input fill:#eef2ff,stroke:#3730a3,color:#1e1b4b
  classDef decision fill:#fff7ed,stroke:#9a3412,color:#431407
  classDef http fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
  classDef ok fill:#dcfce7,stroke:#15803d,color:#14532d
  classDef policy fill:#fef9c3,stroke:#a16207,color:#713f12
  classDef user fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
  classDef err fill:#fde2e2,stroke:#991b1b,color:#450a0a
```

| Colour | Meaning |
|---|---|
| 🟦 blue | network or CSV call site |
| 🟩 green | successful response to caller |
| 🟨 yellow | policy block — caller's request was denied by design (allow-list, read-only guard) |
| 🟥 red | user/input error — bad name, missing manifest entry |
| 🟧 orange | decision point |
| 🟪 indigo | input to the tool |
| 🟥 dark red | sanitised infra error — full trace lives in `logs/app-*.log` under the same `request_id` |

---

## Architecture overview — every tool inherits this pipeline

```mermaid
flowchart LR
  C([MCP client / LLM]):::input --> T[Tool wrapper]:::http
  T --> L[logged_tool decorator<br/>request_id, timer, secret redaction]:::http
  L --> S{Pre-flight checks}:::decision
  S -->|"runmqsc verb<br/>= modify"| RO[/🚫 MODIFY_BLOCKED_MSG<br/>no HTTP/]:::policy
  S -->|"host not<br/>in allow-list"| AL[/🚫 restricted/]:::policy
  S -->|OK| H[/HTTP or local CSV/]:::http
  H -->|success| P[Pretty-print or<br/>JSON envelope]:::ok
  H -->|exception| E[safe_error_message<br/>logs traceback<br/>returns ⚠️ ref id]:::err
  P --> R([Response to caller]):::ok
  E --> R

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef err fill:#fde2e2,stroke:#991b1b
```

**Five mechanisms wired in once, applied to every call:**

| # | Where | What it does |
|---|---|---|
| 1 | `server/safety.py:is_hostname_allowed` | Hostname prefix check against `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES`. |
| 2 | `server/safety.py:is_modification_command` | Blocks `ALTER/DEFINE/DELETE/CLEAR/MOVE/SET/RESET/START/STOP/PURGE/REFRESH/RESOLVE/ARCHIVE/BACKUP` in MQSC. |
| 3 | `server/errors.py:safe_error_message` | Maps exceptions to curated hints, writes traceback to `logs/app-*.log`, returns `⚠️ … (ref <id>)`. |
| 4 | `server/query_log.py:record_endpoint` | Stamps each outbound URL onto the per-call JSONL record. |
| 5 | `mq_helpers._CSV_CACHE`, `ace_helpers._NODE_*_CACHE` | All three CSVs are loaded once, cached in-process; restart to refresh. |

---

# IBM MQ tools (7)

All MQ HTTP traffic goes through a shared `httpx.AsyncClient` with
`MQ_USER_NAME` / `MQ_PASSWORD` Basic Auth and `verify=False`.

## `find_mq_object` — OFFLINE manifest search

```mermaid
flowchart TD
  A([find_mq_object<br/>search_string, object_type?]):::input
  A --> B[/Search qmgr_dump.csv:<br/>qmgr, hostname, mqsc_command, object_type/]:::http
  B --> C{Any matches?}:::decision
  C -->|no| C1{Match exists<br/>without type filter?}:::decision
  C1 -->|no| X1[/❌ '...' not found in manifest/]:::user
  C1 -->|yes| X2[/❌ '...' exists but not of type '...'/]:::user
  C -->|yes| D[Tag each row 'restricted'<br/>via MQ allow-list]:::http
  D --> E{Any accessible?}:::decision
  E -->|no| X3[/🚫 found only on restricted systems/]:::policy
  E -->|yes| OK[/✅ QM:... Host:... Type:...<br/>RESTRICTED rows appended/]:::ok

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
```

**Endpoints recorded:** none (CSV-only).

---

## `dspmq` / `dspmqver` — list QMs / installation info

Same flow for both; only the URL path and prettifier differ.

```mermaid
flowchart TD
  A([dspmq or dspmqver<br/>qmgr_name?]):::input
  A --> B{qmgr_name given?}:::decision
  B -->|no| H[/HTTP GET MQ_URL_BASE/<br/>qmgr/ or installation/]:::http
  B -->|yes| C[Look up host<br/>in qmgr_dump.csv]:::http
  C --> D{QM in manifest?}:::decision
  D -->|no| X1[/❌ Queue Manager not found/]:::user
  D -->|yes| E{Host in allow-list?}:::decision
  E -->|no| X2[/🚫 restricted/]:::policy
  E -->|yes| H
  H --> P[prettify_dspmq or<br/>prettify_dspmqver]:::http
  P --> OK[/✅ name=..., state=...<br/>or per-installation block/]:::ok
  H -. HTTP / timeout / TLS .-> Z[/⚠️ curated hint ref id/]:::err

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
  classDef err fill:#fde2e2,stroke:#991b1b
```

**Endpoints recorded:** the one URL actually hit.

---

## `runmqsc` — single read-only MQSC against a specific QM

Critical: there is **no silent fallback to using the QM name as a hostname**.

```mermaid
flowchart TD
  A([runmqsc<br/>qmgr_name, mqsc_command, hostname?]):::input
  A --> B{First verb in<br/>modify set?}:::decision
  B -->|yes| X1[/🚫 MODIFY_BLOCKED_MSG<br/>NO HTTP/]:::policy
  B -->|no| C{hostname= given?}:::decision
  C -->|yes| F[Use given hostname]:::http
  C -->|no| D[Look up QM<br/>in qmgr_dump.csv]:::http
  D --> E{Found?}:::decision
  E -->|yes| F
  E -->|no| X2[/❌ QM not in manifest<br/>and no hostname — REJECTED/]:::user
  F --> G{Host in allow-list?}:::decision
  G -->|no| X3[/🚫 restricted/]:::policy
  G -->|yes| H[/HTTP POST<br/>host/action/qmgr/qm/mqsc/]:::http
  H --> P[prettify_runmqsc<br/>handles z/OS + distributed]:::http
  P --> R{Any lines<br/>emitted?}:::decision
  R -->|yes| OK[/✅ formatted MQSC output/]:::ok
  R -->|no| OK2[/✅ executed, no diagnostic output/]:::ok
  H -. HTTP / network .-> Z[/⚠️ curated hint ref id/]:::err

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
  classDef err fill:#fde2e2,stroke:#991b1b
```

**Endpoints recorded:** the resolved `…/action/qmgr/<qm>/mqsc` URL.

---

## `run_mqsc_for_object` — discover, then fan out

```mermaid
flowchart TD
  A([run_mqsc_for_object<br/>object_name, mqsc_command, object_type?]):::input
  A --> B{Modify verb?}:::decision
  B -->|yes| X1[/🚫 MODIFY_BLOCKED_MSG/]:::policy
  B -->|no| C[/search_objects_structured<br/>qmgr_dump.csv/]:::http
  C --> D{Any rows?}:::decision
  D -->|no| X2[/❌ not found in manifest/]:::user
  D -->|yes| E[Partition accessible vs restricted]:::http
  E --> F{Any accessible?}:::decision
  F -->|no| X3[/🚫 found only on restricted/]:::policy
  F -->|yes| G[For each accessible QM:<br/>run_mqsc_raw qm, cmd, host]:::http
  G --> G1[Per-QM error stays in its section<br/>others continue]:::http
  G1 --> I{Any restricted?}:::decision
  I -->|yes| J[Append 🚫 'not queried' footer]:::http
  I -->|no| OK[/✅ concatenated per-QM sections/]:::ok
  J --> OK

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
```

**Endpoints recorded:** one `…/action/qmgr/<qm>/mqsc` URL per accessible QM, in order.

---

## `get_queue_depth` — depth across every host, alias-resolved

```mermaid
flowchart TD
  A([get_queue_depth<br/>queue_name]):::input
  A --> B[/search_objects_structured/]:::http
  B --> C{Any rows?}:::decision
  C -->|no| X1[/❌ not found/]:::user
  C -->|yes| D{Any accessible?}:::decision
  D -->|no| X2[/🚫 only on restricted/]:::policy
  D -->|yes| E{Name starts with QA.<br/>OR any row type = QALIAS?}:::decision
  E -->|yes — alias path| G[/DISPLAY QALIAS name/]:::http
  G --> H{TARGET(...) parsed<br/>from response?}:::decision
  H -->|no| Y1[/⚠️ couldn't resolve TARGET<br/>for this QM — continue/]:::err
  H -->|yes| I[/DISPLAY QLOCAL target CURDEPTH/]:::http
  E -->|no — direct path| J[/DISPLAY QLOCAL name CURDEPTH/]:::http
  I --> K[Per-QM output]:::http
  J --> K
  Y1 --> K
  K --> L{Any restricted?}:::decision
  L -->|yes| M[Append restricted footer]:::http
  L -->|no| OK[/✅ depth per accessible QM/]:::ok
  M --> OK

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
  classDef err fill:#fde2e2,stroke:#991b1b
```

**Endpoints recorded:** one or two `…/action/qmgr/<qm>/mqsc` URLs per accessible QM (two on the alias path).

---

## `get_channel_status` — status across every hosting QM

```mermaid
flowchart TD
  A([get_channel_status<br/>channel_name]):::input
  A --> B[/Search with type=CHANNEL/]:::http
  B --> C{Matches?}:::decision
  C -->|yes| E[Partition accessible vs restricted]:::http
  C -->|no| D[/Loosen — search<br/>without type filter/]:::http
  D --> F{Matches?}:::decision
  F -->|no| X1[/❌ not found/]:::user
  F -->|yes| E
  E --> G{Any accessible?}:::decision
  G -->|no| X2[/🚫 only on restricted/]:::policy
  G -->|yes| H[/For each: DISPLAY CHSTATUS name ALL/]:::http
  H --> I{Any restricted?}:::decision
  I -->|yes| J[Append restricted footer]:::http
  I -->|no| OK[/✅ per-QM channel status/]:::ok
  J --> OK

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
```

**Endpoints recorded:** one `…/action/qmgr/<qm>/mqsc` URL per accessible QM.

---

# IBM ACE tools (6)

Five of the six ACE tools route through a single helper, `fetch_ace`,
which encapsulates node resolution + allow-list + HTTP + envelope.

## The `fetch_ace` pipeline (shared by 4 tools)

```mermaid
flowchart TD
  A([fetch_ace node, path, component]):::input
  A --> B[get_node_endpoint:<br/>look up host:port<br/>in node_config.csv]:::http
  B --> C{Node found?}:::decision
  C -->|no| X1[/❌ JSON envelope:<br/>'Integration Node not defined'/]:::user
  C -->|yes| D{Host in ACE<br/>allow-list?}:::decision
  D -->|no| X2[/🚫 JSON envelope:<br/>restricted message/]:::policy
  D -->|yes| E[/HTTPS GET<br/>host:port/apiv2 + path/]:::http
  E --> F{response.json works?}:::decision
  F -->|yes| OK[/✅ JSON success envelope<br/>component, raw_response/]:::ok
  F -->|no| OK2[/✅ JSON success envelope<br/>raw_response = text:.../]:::ok
  E -. HTTP error / timeout .-> Z[/⚠️ JSON envelope:<br/>curated hint ref id/]:::err

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef policy fill:#fef9c3,stroke:#a16207
  classDef user fill:#fee2e2,stroke:#b91c1c
  classDef err fill:#fde2e2,stroke:#991b1b
```

`fetch_ace` **never raises** — every leaf above is a JSON envelope string.

### Which path each tool calls (and what it projects from the response)

| Tool | Path under `/apiv2` | Returned shape |
|---|---|---|
| `get_ace_node_status` | `<empty>` (i.e. `/apiv2`) | only `properties` + `descriptiveProperties` |
| `list_ace_servers` | `/servers?depth=2` | `servers: [{name, active, properties}, …]` |
| `list_ace_applications` | `/servers/<server>/applications?depth=2` | `raw_response.children: [{name, properties, descriptiveProperties, active}, …]` |
| `list_ace_message_flows` (no `app`) | `/servers/<server>/messageflows?depth=2` | raw envelope |
| `list_ace_message_flows` (`app` given) | `/servers/<server>/applications/<app>/messageflows?depth=2` | raw envelope |

> An unknown `server` or `app` is **not** validated locally — the upstream
> 404 surfaces as a sanitised `Endpoint not found (ref …)` envelope.

---

## `list_ace_nodes` — OFFLINE node config

```mermaid
flowchart TD
  A([list_ace_nodes]):::input
  A --> B[/Load node_config.csv/]:::http
  B --> C{Empty / missing?}:::decision
  C -->|yes| X1[/❌ JSON envelope:<br/>'No ACE nodes configured'/]:::user
  C -->|no| OK[/✅ JSON: status=success<br/>configured_nodes=.../]:::ok

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef user fill:#fee2e2,stroke:#b91c1c
```

**Endpoints recorded:** none (CSV-only).

---

## `search_ace_local_dump` — OFFLINE BIP-message search

```mermaid
flowchart TD
  A([search_ace_local_dump<br/>search_string]):::input
  A --> B[/Load node_dump.csv/]:::http
  B --> C[Substring match<br/>across all string columns]:::http
  C --> D{Any matches?}:::decision
  D -->|yes| OK[/✅ JSON: results=.../]:::ok
  D -->|no| E{node_dump.csv<br/>actually loaded?}:::decision
  E -->|no — missing/empty| X1[/❌ JSON: 'node_dump.csv empty or missing'/]:::user
  E -->|yes| X2[/✅ JSON: success, results=[]/]:::ok

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef user fill:#fee2e2,stroke:#b91c1c
```

**Endpoints recorded:** none (CSV-only).

---

## `get_cert_details` — OFFLINE certificate-inventory search

```mermaid
flowchart TD
  A([get_cert_details<br/>search_string]):::input
  A --> B[/Load cert_dump.csv/]:::http
  B --> C[Substring match across all columns<br/>hostname, alias, cnname, validfrom,<br/>validuntil, expiry]:::http
  C --> D{Any matches?}:::decision
  D -->|yes| OK[/✅ JSON: results=[{...6 fields}]/]:::ok
  D -->|no| E{cert_dump.csv<br/>actually loaded?}:::decision
  E -->|no — missing/empty| X1[/❌ JSON: 'cert_dump.csv empty or missing'/]:::user
  E -->|yes| X2[/✅ JSON: success, results=[]/]:::ok

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef decision fill:#fff7ed,stroke:#9a3412
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef user fill:#fee2e2,stroke:#b91c1c
```

Returns one row per matching certificate with `hostname`, `alias`, `cnname`
(the CN/subject), `validfrom`, `validuntil`, and `expiry` (the validity span in
days = `validuntil − validfrom`). The search matches the string against ALL
columns, so a user can look up by hostname, alias, or CN. No live endpoint is
inspected — freshness depends on the extract that produced `cert_dump.csv`.

**Endpoints recorded:** none (CSV-only).

---

# Recovery — turning a `⚠️ … (ref <id>)` back into a root cause

```mermaid
flowchart LR
  U([User sees:<br/>⚠️ hint ref id]):::err
  U --> A[Open logs/app-YYYY-MM-DD.log]:::http
  A --> B[grep for request_id<br/>→ full traceback + URL + body]:::http
  B --> C[Open logs/queries-YYYY-MM-DD.jsonl]:::http
  C --> D[Match request_id<br/>→ tool, args, endpoints, latency, caller]:::http
  D --> OK([Root cause]):::ok

  classDef input fill:#eef2ff,stroke:#3730a3
  classDef http fill:#dbeafe,stroke:#1d4ed8
  classDef ok fill:#dcfce7,stroke:#15803d
  classDef err fill:#fde2e2,stroke:#991b1b
```

For `🚫 …` messages the cause is policy, not a fault:

| Message | Policy | Fix |
|---|---|---|
| Hostname not in allowed list | `MQ_/ACE_ALLOWED_HOSTNAME_PREFIXES` | Add the prefix in `.env` and restart, or pick a host in the list. |
| Modification requests not permitted | `is_modification_command` block | Use a DISPLAY variant, or open a ServiceNow ticket per the message. |
| Found only on restricted systems | Object exists but every hosting host is outside the allow-list | Same as above — adjust the allow-list deliberately. |

The server never auto-retries — recovery is at the orchestrator or
operator level. Backoff / circuit-breaker are intentionally out of
scope (see CLAUDE.md → "Things that are deliberately NOT done").
