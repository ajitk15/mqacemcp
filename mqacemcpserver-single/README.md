# mqacemcpserver-single

A sibling MCP server for environments where the orchestrator/frontend can only
invoke **one tool per user turn** — no parallel tool calls, no sequential
ReAct-style chaining. Each of the seven tools below is self-sufficient: it
performs the full discovery-plus-execution workflow internally and returns one
consolidated answer.

To cover "X **and** Y" questions without chaining, every tool takes a **list**
for its primary target(s), so several objects of the same kind can be handled
in a single call (e.g. `queue_names=["QL.IN.APP1","QL.IN.APP2"]`,
`nodes=["NODE1","NODE2"]`, `qmgr_names=["QM1","QM2"]`). For `ace_server_explore`
the servers list shares one `node`; for `mq_host_overview` the `mqsc_command`
applies to every queue manager listed.

This is a deployment variant. The root server (`mqacemcpserver`) and its
chatbot are untouched. The two can run side-by-side on different ports
against the same upstream MQ / ACE infrastructure and the same CSV manifests.

## Tool catalogue at a glance

| Tool | Replaces (from root server) | One-call intent |
| --- | --- | --- |
| `mq_queue_inspect` | `find_mq_object` + `get_queue_depth` + queue `runmqsc` | "what's the depth / config of queue X" (alias-aware) |
| `mq_channel_inspect` | `find_mq_object` + `get_channel_status` + channel `runmqsc` | "is channel X up, with what SSL / CONNAME / batch settings" |
| `mq_host_overview` | `dspmq` + `dspmqver` + read-only `runmqsc` | "tell me about this host / QM, optionally with one DISPLAY" |
| `ace_node_overview` | `list_ace_nodes` + `get_ace_node_status` + `list_ace_servers` | "what's on node N1" |
| `ace_server_explore` | `list_ace_applications` + `list_ace_message_flows` | "what's deployed on server X on N1" |
| `ace_search` | `list_ace_nodes` (listing) + `search_ace_local_dump` | "find any ACE thing matching X (nodes / BIP log)" |
| `get_cert_details` | (new — no root-server equivalent) | "when does the cert on host / alias / CN X expire" |

Each tool keeps the same safety contract as the root server:

- Read-only MQSC enforced via `is_modification_command`.
- Hostname allow-list enforced before every outbound HTTP call.
- All exceptions routed through `safe_error_message` — never raw upstream text.
- All endpoints recorded in `queries-YYYY-MM-DD.jsonl` for audit.

---

## Tool reference

### 1. `mq_queue_inspect`

**IBM MQ.** Inspect a queue end-to-end in a single call. Bundles manifest
discovery + alias resolution + a **full attribute fetch** (`DISPLAY QLOCAL … ALL`),
so it answers any queue-property question (depth, persistence `DEFPSIST`,
`MAXMSGL`, priority, get/put, triggering, creation / last-altered dates, …).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `queue_names` | `list[str]` | yes | One or more queue names (`QL.*`, `QA.*`, `QR.*`, or any other), as a list — e.g. `["QL.IN.APP1"]` or `["QL.IN.APP1","QL.IN.APP2"]`. Each is inspected independently and the results concatenated. |
| `qmgr_name` | `str` | no | When given, skips manifest discovery and goes straight to that QM (FAST PATH). Applies to every queue in `queue_names`. |
| `hostname` | `str` | no | Explicit host. Wins over manifest lookup when both are present. |

**What it does internally**
- If `qmgr_name` is supplied → resolves host, allow-list check, runs `DISPLAY QLOCAL(<Q>) ALL` / `QALIAS` / `QREMOTE(<Q>) ALL` (chosen by prefix or `object_type`) on that QM only.
- Otherwise → searches `qmgr_dump.csv`, branches on count of hosting QMs, runs the inspection per accessible QM.
- For `QA.*` aliases: runs `DISPLAY QALIAS(<Q>)`, parses `TARGET(...)`, then runs `DISPLAY QLOCAL(<target>) ALL` (the target's full attribute set).
- Restricted hosts are surfaced explicitly (never "does not exist").

**Sample user questions it answers in one call**
- "What's the depth of QL.IN.APP1?"
- "What's the depth of QL.IN.APP1 and QL.IN.APP2?" → `queue_names=["QL.IN.APP1","QL.IN.APP2"]` (both in one call)
- "Depth of QL.ORDERS on MQQMGR1"
- "Where is QL.IN.APP1 hosted?"
- "Is QL.IN.APP1 triggered?"
- "What is the persistence of QL.IN.APP1?" (reads `DEFPSIST` from the full attrs)
- "When was QL.IN.APP1 created / last altered?" (`CRDATE CRTIME` / `ALTDATE ALTTIME`)
- "Open handles / IPPROCS / OPPROCS on QL.ORDERS on QM1"
- "What's the max depth of QL.ORDERS?"
- "Resolve alias QA.IN.APP1 and give me its target's depth"
- "Definition of QR.IN.APP2 (remote queue)"

---

### 2. `mq_channel_inspect`

**IBM MQ.** Inspect a channel end-to-end in a single call. Returns BOTH
runtime status AND configuration per hosting queue manager.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `channel_names` | `list[str]` | yes | One or more MQ channel names, as a list — e.g. `["CH.TO.PARTNER"]` or `["CH.TO.PARTNER","CH.SDR.TO.QM2"]`. Each is inspected independently and the results concatenated. |
| `qmgr_name` | `str` | no | When given, FAST PATH on that QM. Applies to every channel in `channel_names`. |
| `hostname` | `str` | no | Explicit host. Wins over manifest lookup. |

**What it does internally**
- Discovery branches the same way `mq_queue_inspect` does (FAST PATH if QM given, manifest discovery otherwise).
- Per hosting QM, runs two MQSC commands concurrently:
  - `DISPLAY CHSTATUS(<C>) ALL` — runtime status (state, msgs, lastmsg, conname, bytes).
  - `DISPLAY CHANNEL(<C>) CHLTYPE CONNAME SSLCIPH SSLPEER CERTLABL MAXMSGL BATCHSZ HBINT` — configuration.

**Sample user questions it answers in one call**
- "Is channel CH.APP.SVRCONN up?"
- "Are CH.APP.SVRCONN and CH.TO.PARTNER up?" → `channel_names=["CH.APP.SVRCONN","CH.TO.PARTNER"]` (both in one call)
- "Status of CH.TO.PARTNER on QM3"
- "SSL cipher on CH.TO.PARTNER on QM3"
- "What's the CONNAME of CH.SDR.TO.QM2?"
- "Batch size and heartbeat for CH.SDR.TO.QM2"
- "Which channel type is CH.APP.SVRCONN?"
- "Where does channel CH.TO.PARTNER run?" (no QM given → discovery)

---

### 3. `mq_host_overview`

**IBM MQ.** Host-level overview — `dspmq` + `dspmqver`, plus one optional
read-only MQSC command.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `qmgr_names` | `list[str]` | no | One or more queue managers to target (each resolved to a host via the manifest) — e.g. `["QM1"]` or `["QM1","QM2"]`. |
| `hostnames` | `list[str]` | no | One or more explicit hosts — e.g. `["lopalhost"]`. An explicit host is used directly (skips manifest lookup). |
| `mqsc_command` | `str` | no | One read-only `DISPLAY` MQSC. **Requires a queue manager** (applied to every QM in `qmgr_names`). Modification verbs (ALTER/DEFINE/…) are blocked. |

**Target resolution (per target)**
1. A single `qmgr_names` + single `hostnames` is treated as one paired target (run the MQSC on that QM via that explicit host).
2. Otherwise each entry in `qmgr_names` (resolved via manifest) and each entry in `hostnames` (used directly) is a separate target.
3. With no targets at all, the configured default `MQ_URL_BASE` is used.

**What it returns**
- `dspmq` section: every queue manager on the resolved host with its state.
- `dspmqver` section: MQ installation name, version, architecture, install path.
- If a queue-manager target + `mqsc_command` given: appends the MQSC output (or
  `MODIFY_BLOCKED_MSG` if the verb is not read-only).
- With multiple targets, the per-target overviews are concatenated under a banner.

**Sample user questions it answers in one call**
- "Run dspmq on host lopalhost"
- "List queue managers on lopalhost"
- "What MQ version is installed on QM1's host?"
- "MQ version on QM1 and QM2" → `qmgr_names=["QM1","QM2"]` (both in one call)
- "dspmqver on QM1"
- "List all listeners on QM1" → `mqsc_command="DISPLAY LSSTATUS(*) ALL"`
- "Show QM1's configuration" → `mqsc_command="DISPLAY QMGR ALL"`
- "Is there a dead letter queue on QM1?" → `mqsc_command="DISPLAY QMGR DEADQ"`
- "Show all channels on QM1" → `mqsc_command="DISPLAY CHANNEL(*) CHLTYPE"`

---

### 4. `ace_node_overview`

**IBM ACE.** Node-level overview — node status + every integration server on
that node, in one call.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `nodes` | `list[str]` | yes | One or more integration node names — e.g. `["NODE2"]` or `["NODE1","NODE2"]`. |

**What it does internally**
- For each node, concurrently issues `GET /apiv2` (node-level) and `GET /apiv2/servers?depth=2` against the node's admin REST endpoint.
- A single node returns one envelope: `{node, status, properties, descriptiveProperties, servers:[{name, active, properties}]}`. Multiple nodes return `{status, count, nodes:[<envelope>, …]}`.

**Sample user questions it answers in one call**
- "What's running on NODE2?"
- "What's running on NODE1 and NODE2?" → `nodes=["NODE1","NODE2"]` (both in one call)
- "Is integration server IS001 active on NODE2?"
- "What ACE version is NODE2 running?"
- "What's NODE2's REST admin port?"
- "List all integration servers on NODE2"
- "Is NODE2's broker up?"
- "How many integration servers does NODE2 have?"

---

### 5. `ace_server_explore`

**IBM ACE.** Explore one or more integration servers — applications + message
flows in one call.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `node` | `str` | yes | Integration node name (shared by all servers). |
| `servers` | `list[str]` | yes | One or more integration server names on that node — e.g. `["IS001"]` or `["IS001","IS002"]`. |
| `application` | `str` | no | Optional. When given, message flows are scoped to that application (applied to every server); otherwise flows directly on the server are returned alongside the application list. |

**What it does internally**
- For each server, concurrently fetches applications and message flows (scoped or unscoped).
- A single server returns one envelope: `{node, server, application?, applications:[…], message_flows:[…]}`. Multiple servers return `{status, node, count, servers:[<envelope>, …]}`.

**Sample user questions it answers in one call**
- "What apps are deployed on IS001 on NODE2?"
- "What apps are on IS001 and IS002 on NODE2?" → `node="NODE2", servers=["IS001","IS002"]` (both in one call)
- "List flows on IS001 on NODE2"
- "What message flows are in snaplogic1 on IS001 on NODE2?"
- "Is application snaplogic1 running on IS001?"
- "What's deployed under server snaps on NODE2?"

---

### 6. `ace_search`

**IBM ACE.** Combined OFFLINE search across configured nodes and the BIP
message dump, in one call.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `search_strings` | `list[str]` | yes | One or more substrings to match, case-insensitive (a row matches if it matches ANY of them; matches are merged + de-duplicated). Pass `[""]` (or an empty list) with `scope="nodes"` to list every configured node. |
| `scope` | `str` | no | `"nodes"` / `"dump"` / `"all"` (default `"all"`). |

**What it does internally**
- `scope="nodes"` → reads `node_config.csv`, ORing the substrings across rows.
- `scope="dump"` → calls `search_node_dump(s)` per string over `node_dump.csv`, merged + de-duplicated.
- `scope="all"` (default) → both sections in one envelope.

**Sample user questions it answers in one call**
- "List all integration nodes" → `search_strings=[""], scope="nodes"`
- "Find any node matching lodace01.example.com" → `scope="nodes"`
- "Any BIP errors mentioning OrderFlow?" → `scope="dump"`
- "Any BIP errors mentioning OrderFlow or PaymentFlow?" → `search_strings=["OrderFlow","PaymentFlow"], scope="dump"` (match either, one call)
- "Find BIP1290 messages" → `scope="dump"`
- "Search every ACE source for 'snaplogic1'" → `scope="all"` (default)
- "Which nodes are on port 4415?" → `scope="nodes"`, `search_strings=["4415"]`

---

### 7. `get_cert_details`

**Certificates.** OFFLINE lookup of TLS/SSL certificate details from
`resources/cert_dump.csv` (shared with the root server), in one call.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| `search_strings` | `list[str]` | yes | One or more hostname/alias/CN substrings to match (case-insensitive, against ALL columns), as a list — e.g. `["lodmq01"]` or `["lodmq01","lotace03"]`. Matches from all queries are merged and de-duplicated. |

**What it does internally**
- `search_certs(s)` → case-insensitive substring search across every column of
  `cert_dump.csv`, run once per supplied string; matches are merged and
  de-duplicated by `(hostname, alias, cn_name)`.
- Returns a JSON envelope: `{status, message, results:[{hostname, alias,
  cn_name, valid_from, valid_until, expirydays, ace_nodes, matched_query}]}`.
  `valid_until` is the certificate's expiry date; `expirydays` is the whole-day
  count until it, recomputed live against today (negative if already expired);
  `ace_nodes` is the ACE node(s) running on that hostname per `node_dump.csv`
  (empty for a pure-MQ host); `matched_query` lists which of the supplied search
  strings matched this row. No live endpoint is inspected.

**Sample user questions it answers in one call**
- "When does the certificate on lodmq01 expire?"
- "When do the certs on lodmq01 and lotace03 expire?" → `search_strings=["lodmq01","lotace03"]` (both in one call)
- "Show cert details for alias mqweb-https"
- "Which certs are issued for example.com?"
- "What's the CN on the lotace03 certificate?"

---

## Internal function call graph

For each composite tool, the helpers it invokes (in call order) and what
flows in/out of each. Format: `N.M  helper : description | input | output`.

Helpers prefixed with `_` are private to `server/composite_tools.py`. All
others live in `server/mq_helpers.py`, `server/ace_helpers.py`,
`server/safety.py`, or `server/errors.py`.

### 1. `mq_queue_inspect` : inspect a queue end-to-end (manifest discovery + alias resolution + full attrs via `DISPLAY QLOCAL … ALL`)

  1.1 `_resolve_target_host` : look up the hostname for a known QM (FAST PATH only) | in: `qmgr_name`, optional `explicit_hostname` | out: `(hostname: str | None, error_message: str | None)`

  1.2 `hostname_allowed` (MQ) : check resolved hostname against `MQ_ALLOWED_HOSTNAME_PREFIXES` before any HTTP | in: `hostname` | out: `(allowed: bool, blocked_message: str)`

  1.3 `search_objects_structured` : DISCOVERY PATH — search `qmgr_dump.csv` for the queue | in: `search_string`, optional `object_type` | out: `list[{qmgr, hostname, object_type, restricted}]`

  1.4 `_inspect_queue_on_qm` : run the queue-inspect MQSC chain on ONE queue manager (auto-detects QA/QR/QL and follows the alias `TARGET` when present) | in: `qmgr`, `queue_name`, `hostname`, optional `hint_type` | out: formatted text block (alias mapping if applicable + QLOCAL/QREMOTE details)

  1.5 `run_mqsc_raw` (called by 1.4) : execute one read-only MQSC against MQ REST and prettify | in: `qmgr_name`, `mqsc_command`, `target_hostname` | out: prettified MQSC text (or sanitised error message on failure)

  1.6 `_restricted_footer` : build the "🚫 also on restricted hosts" trailer | in: `list[restricted_entries]` | out: text (empty when no restricted hits)

### 2. `mq_channel_inspect` : inspect a channel end-to-end (runtime status + configuration per hosting QM)

  2.1 `_resolve_target_host` (FAST PATH only) — same as 1.1

  2.2 `hostname_allowed` (MQ) — same as 1.2

  2.3 `search_objects_structured` : DISCOVERY PATH | in: `channel_name`, `object_type="CHANNEL"` (with fallback to no type) | out: same shape as 1.3

  2.4 `_inspect_channel_on_qm` : run `DISPLAY CHSTATUS(<C>) ALL` and `DISPLAY CHANNEL(<C>) CHLTYPE CONNAME SSLCIPH SSLPEER CERTLABL MAXMSGL BATCHSZ HBINT` concurrently | in: `qmgr`, `channel_name`, `hostname` | out: formatted text block with status + config sections

  2.5 `run_mqsc_raw` (called twice inside 2.4 via `asyncio.gather`) — same as 1.5

  2.6 `_restricted_footer` — same as 1.6

### 3. `mq_host_overview` : host-level overview (`dspmq` + `dspmqver` + optional read-only MQSC)

  3.1 `_resolve_target_host` : only when `qmgr_name` is supplied without an explicit `hostname` — same as 1.1

  3.2 `hostname_allowed` (MQ) : skipped only when the call falls through to the default `MQ_URL_BASE` (no host substitution) — same as 1.2

  3.3 `build_url` : substitute the resolved host into `MQ_URL_BASE` and append a path | in: `target_hostname`, `path` | out: full URL string

  3.4 `mq_get` (called twice via `asyncio.gather`) : HTTP GET to MQ REST; records the URL on the in-flight audit record | in: `url`, kwargs | out: `httpx.Response`

  3.5 `prettify_dspmq` : flatten dspmq JSON into one-line-per-QM text | in: response bytes | out: `name=QM1, state=running\n...`

  3.6 `prettify_dspmqver` : flatten dspmqver JSON into a multi-line block | in: response bytes | out: text with `Name / Version / Architecture / Installation Path`

  3.7 `is_modification_command` : refuse `ALTER / DEFINE / DELETE / CLEAR / MOVE / SET / RESET / START / STOP / PURGE / REFRESH / RESOLVE / ARCHIVE / BACKUP` | in: `mqsc_command` | out: `bool` (when True, the composite returns `MODIFY_BLOCKED_MSG` instead of running it)

  3.8 `run_mqsc_raw` : only when both `mqsc_command` and `qmgr_name` are supplied AND the verb is read-only — same as 1.5

  3.9 `friendly_error` : sanitise any upstream exception so the user never sees raw text | in: `Exception`, optional `hostname` | out: `⚠️ <hint> (ref <id>)` text

### 4. `ace_node_overview` : node overview (node status + integration servers in one envelope)

  4.1 `fetch_ace` (called twice concurrently via `asyncio.gather`) : call ACE Admin REST and wrap the result in a JSON envelope | in: `target_node`, `path`, `component`, **kwargs | out: JSON string `{status, component, runtime_state, raw_response}` on success, or `{status:"error", message, details}` on failure
   - call A — `path=""` → node-level properties + version
   - call B — `path="/servers?depth=2"` → integration-server list with `name`, `active`, `properties`

  Internals that `fetch_ace` itself triggers (for traceability):
   - `get_node_endpoint` : resolve node → `(host, port)` from `node_config.csv` | in: `node` | out: `(host: str, port: int)` (raises `ValueError` if unknown — caught and wrapped)
   - `hostname_allowed` (ACE) : ACE-specific allow-list (`ACE_ALLOWED_HOSTNAME_PREFIXES`) | in: `hostname` | out: `(bool, blocked_message)`
   - `record_endpoint` : append URL to the audit-log record | in: `url` | out: side-effect, no return
   - `safe_error_message` : on any caught exception | in: `Exception`, optional `hint`, optional `extra` | out: `⚠️ ... (ref ...)` text

### 5. `ace_server_explore` : explore one integration server (applications + message flows)

  5.1 `fetch_ace` (called twice concurrently via `asyncio.gather`) — same shape as 4.1
   - call A — `path="/servers/{server}/applications?depth=2"` → apps with `name`, `active`, `properties`, `descriptiveProperties`
   - call B — either `path="/servers/{server}/applications/{app}/messageflows?depth=2"` (when `application` arg supplied) or `path="/servers/{server}/messageflows?depth=2"` (server-direct flows)

### 6. `ace_search` : combined OFFLINE search across configured nodes + BIP dump (no upstream HTTP)

  6.1 `load_node_config` : cached read of `resources/node_config.csv` (loads on first call, then returns the cached DataFrame) | in: none | out: pandas DataFrame with columns `node, host, nodeport`

  6.2 `load_node_dump` : cached read of `resources/node_dump.csv` (used here as an "empty / missing" check before searching) | in: none | out: pandas DataFrame

  6.3 `search_node_dump` : case-insensitive substring search across all string columns of `node_dump.csv` | in: `search_string` | out: `list[{timestamp, host, node, status}]`

### 7. `get_cert_details` : OFFLINE certificate inventory lookup (no upstream HTTP)

  7.1 `load_cert_dump` : cached read of `resources/cert_dump.csv` (used as an "empty / missing" check before searching) | in: none | out: pandas DataFrame with columns `hostname, alias, cn_name, valid_from, valid_until, expirydays`

  7.2 `search_certs` : case-insensitive substring search across all columns of `cert_dump.csv`; recomputes `expirydays` live from `valid_until` | in: `search_string` | out: `list[{hostname, alias, cn_name, valid_from, valid_until, expirydays}]`

  7.3 `nodes_on_host` (from `ace_helpers`) : distinct ACE node names on a hostname (exact match against `node_dump.csv`), used to add `ace_nodes` to each cert result | in: `hostname` | out: `list[str]`

---

## Layout

```
mqacemcpserver-single/
├── single_server.py           # Entry point (stdio / SSE / Basic Auth / TLS / healthz)
├── server/
│   ├── composite_tools.py     # The 6 composite tools + get_cert_details (the only new code in this build)
│   ├── config.py              # env loader, with RESOURCES_DIR override pointing at ../resources/
│   ├── mq_helpers.py          # MQ REST client, qmgr_dump.csv reader, MQSC prettifiers
│   ├── ace_helpers.py         # ACE Admin REST client, node CSVs, fetch_ace
│   ├── cert_helpers.py        # cert_dump.csv reader + substring search + live expiry-days
│   ├── csv_cache.py           # mtime-based auto-reload cache for all CSV manifests (+ /healthz freshness)
│   ├── safety.py              # hostname allow-list + modification-MQSC guard
│   ├── errors.py              # safe_error_message — sanitises every upstream exception
│   ├── query_log.py           # @logged_tool decorator + per-call JSONL audit log
│   ├── logger.py              # rotating app log
│   └── auth.py                # SSE Basic Auth middleware
├── clients/
│   └── smoke_test.py          # 37-case live SSE smoke client (see Testing → Online smoke)
├── prompts/
│   └── composite_system.md    # Reference system prompt ({scope_block}/{tool_catalog} placeholders) — see "System prompt"
├── tests/
│   ├── conftest.py            # Sets temp LOG_DIR before importing server.*
│   ├── test_composite_tools.py # 25 offline tests
│   └── test_csv_cache.py      # 4 tests — manifest auto-reload + freshness
├── requirements.txt           # Same as root: mcp, httpx, pandas, python-dotenv, uvicorn
├── .env.example               # Template; LOG_DIR=logs-single, MCP_PORT=8443 (or 8010)
└── README.md
```

## System prompt (`prompts/composite_system.md`)

`composite_system.md` is a **reference** system prompt for whatever orchestrator
drives this server's tools. **The MCP server itself never reads it** — this build
is a pure tool server with no LLM. It is provided so a host can adopt the same
routing/rendering guidance the tools were designed for.

It carries two substitution placeholders (the same contract the bundled chatbot
uses — see `backend/agent.py`):

| Placeholder | Replaced with |
| --- | --- |
| `{scope_block}` | A domain-guardrail block when `BOT_DOMAIN` is set (the assistant refuses out-of-scope questions and points to a support team); an **empty string** when `BOT_DOMAIN` is unset. It's the on/off switch for the scope guardrail, keeping the refusal wording centralised in the host rather than hard-coded in the prompt. |
| `{tool_catalog}` | The bullet list of currently exposed tools, generated from the live catalogue. |

Contract notes:
- A consumer must perform the substitution before sending the prompt to the LLM.
  The chatbot's loader (`agent.py`) **validates both placeholders are present and
  skips any prompt file missing them**, so keep both if you point a host at this
  file via `SYSTEM_PROMPT_FILE`.
- If your orchestrator feeds the markdown to the model **without** substitution,
  the literal `{scope_block}` would leak into the prompt — delete that line or
  replace it with your own static scope instructions.

## Quickstart (Windows / PowerShell)

```powershell
cd C:\Workspace\hready\mqacemcp\mqacemcpserver-single

# venv + deps (same versions as root)
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Configure
Copy-Item .env.example .env
# edit .env to set MQ_URL_BASE / MQ_USER_NAME / MQ_PASSWORD, and
# (for SSE) MCP_AUTH_USER / MCP_AUTH_PASSWORD

# Run (stdio, default)
.venv\Scripts\python.exe single_server.py

# Run (SSE on :8010, with /healthz at /healthz)
$env:MCP_TRANSPORT = "sse"
.venv\Scripts\python.exe single_server.py

# Smoke check — must print exactly 7 tool names
.venv\Scripts\python.exe -c "import single_server as m; print(sorted(m.mcp._tool_manager._tools.keys()))"

# Live smoke test (37 cases via SSE) — see "Testing → Online smoke" below
.venv\Scripts\python.exe clients\smoke_test.py
```

## Testing

The test suite is **fully offline** — no live MQ or ACE infrastructure is
required. Tests run against the shared CSV manifests under `../resources/`
and verify catalogue stability, allow-list enforcement, read-only enforcement,
and graceful error envelopes for the unreachable-upstream branches.

`pytest` and `pytest-asyncio` are dev-only dependencies and are NOT in
`requirements.txt` (same convention as the root project). Install them once
in the venv you use to run the server:

```powershell
cd C:\Workspace\hready\mqacemcp\mqacemcpserver-single
.venv\Scripts\python.exe -m pip install pytest pytest-asyncio
```

### Run the full suite

```powershell
.venv\Scripts\python.exe -m pytest -q
# Expected: 21 passed in ~3s
```

### Useful pytest invocations

```powershell
# Verbose — one line per test with PASS/FAIL
.venv\Scripts\python.exe -m pytest -v

# Single file
.venv\Scripts\python.exe -m pytest tests\test_composite_tools.py -q

# Filter by test name (substring match on the function name)
.venv\Scripts\python.exe -m pytest -k "allow_list" -v
.venv\Scripts\python.exe -m pytest -k "ace_search" -v
.venv\Scripts\python.exe -m pytest -k "modification" -v

# Stop on first failure, show full traceback
.venv\Scripts\python.exe -m pytest -x --tb=long

# Show stdout / log output (useful when a test prints diagnostics)
.venv\Scripts\python.exe -m pytest -s
```

### What the 25 tests cover

| # | Group | Test | What it asserts |
| --- | --- | --- | --- |
| 1 | catalogue | `test_exactly_seven_tools_registered` | tool set == exactly the 7 tools (no missing, no extras) |
| 2 | catalogue | `test_mq_tool_docstrings_open_with_routing_prefix` | every MQ tool's docstring starts with `IBM MQ:` |
| 3 | catalogue | `test_ace_tool_docstrings_open_with_routing_prefix` | every ACE tool's docstring starts with `IBM ACE:` |
| 4 | catalogue | `test_cert_tool_docstring_opens_with_routing_prefix` | `get_cert_details` docstring starts with `Certificate:` |
| 5 | mq_queue_inspect | `test_mq_queue_inspect_not_in_manifest` | unknown queue → "not found in the manifest" hint |
| 6 | mq_queue_inspect | `test_mq_queue_inspect_restricted_only` | manifest hit on a restricted host → "restricted" message |
| 7 | mq_queue_inspect | `test_mq_queue_inspect_fast_path_rejects_disallowed_host` | FAST PATH + `hostname="evil-host"` → allow-list reject |
| 8 | mq_channel_inspect | `test_mq_channel_inspect_not_in_manifest` | unknown channel → not-found hint |
| 9 | mq_channel_inspect | `test_mq_channel_inspect_fast_path_rejects_disallowed_host` | FAST PATH + bad host → allow-list reject |
| 10 | mq_host_overview | `test_mq_host_overview_blocks_modification_mqsc` | `mqsc_command="DEFINE QLOCAL(X)"` → `MODIFY_BLOCKED_MSG` |
| 11 | mq_host_overview | `test_mq_host_overview_rejects_disallowed_host` | `hostname="evil-host"` → allow-list reject before HTTP |
| 12 | mq_host_overview | `test_mq_host_overview_warns_when_mqsc_without_qmgr` | `mqsc_command` without `qmgr_name` → warning, not executed |
| 13 | ace_search | `test_ace_search_rejects_unknown_scope` | `scope="bogus"` → error envelope |
| 14 | ace_search | `test_ace_search_nodes_scope_lists_configured_nodes` | `scope="nodes"` returns rows from `node_config.csv` |
| 15 | ace_search | `test_ace_search_dump_scope_filters_by_substring` | `scope="dump"` matches genuinely contain the substring |
| 16 | ace_search | `test_ace_search_default_scope_returns_both_sections` | no scope ⇒ both `nodes` and `dump_matches` present |
| 17 | ace_node_overview | `test_ace_node_overview_unknown_node` | unknown node → graceful envelope (no exception) |
| 18 | ace_server_explore | `test_ace_server_explore_unknown_node` | unknown node → graceful envelope (no exception) |
| 19 | get_cert_details | `test_get_cert_details_no_match_returns_empty_results` | no-match → success envelope with empty `results` |
| 20 | get_cert_details | `test_get_cert_details_match_returns_expected_fields` | match returns all six cert fields |
| 21 | get_cert_details | `test_get_cert_details_searches_all_fields` | alias-only substring still matches (all-column search) |
| 22 | ace_search | `test_ace_search_dump_pivots_cert_host_to_node` | a cert hostname (`lodace01.example.com`) resolves to its node (`NODE01`) via the aligned `node_dump.csv` |
| 23 | get_cert_details | `test_get_cert_details_exposes_expirydays` | every match carries an integer-parseable `expirydays` (computed live) |
| 24 | get_cert_details | `test_get_cert_details_includes_ace_nodes` | result includes `ace_nodes` — `["NODE01"]` for an ACE host, `[]` for a pure-MQ host |
| 25 | mq_queue_inspect | `test_mq_queue_inspect_local_queue_displays_all_attributes` | a local-queue inspect issues `DISPLAY QLOCAL(<Q>) ALL` (full attribute set, not the old fixed subset) |

### Test conventions

- `tests/conftest.py` redirects `LOG_DIR` to a temp directory **before** the
  `server.*` modules are imported (env vars must be set first — do not move
  this fixture into the test module or import from `server.*` at the top of
  `conftest.py`).
- The shipped `resources/qmgr_dump.csv` ships with hostnames (`lopalhost`,
  `lodalhost`) that are NOT in the default `lod,loq,lot` allow-list when
  using `.env.example` defaults. Test #5 depends on that to exercise the
  restricted-only branch; if you broaden the allow-list in `.env`, that test
  becomes an attempted live call.
- No tests make outbound HTTP — every assertion lands in the discovery,
  validation, or sanitisation layer before any network call would happen.

### Online smoke (clients/smoke_test.py)

A separate live-deployment smoke client that opens an MCP SSE session,
lists the catalogue, then drives **44 test cases** across the seven
tools (including a multi-target case for every tool that takes a list).
Unlike the offline pytest suite, this one requires the server to be running
and (for `live` cases) the configured upstream MQ / ACE infrastructure to be
reachable.

**Prerequisites**
- Server running on SSE — see [Quickstart](#quickstart-windows--powershell).
- `mcp`, `httpx`, `python-dotenv` already in the venv (all in `requirements.txt`).
- The same `.env` the server uses (the client reads it for `MCP_AUTH_USER`,
  `MCP_AUTH_PASSWORD`, `MCP_HOST`, `MCP_PORT`, `MCP_TLS_CERT/KEY`).

**Run it**

```powershell
cd C:\Workspace\hready\mqacemcp\mqacemcpserver-single
.venv\Scripts\python.exe clients\smoke_test.py
# Expected: pass=43  skip=1  fail=0  (skip = NODE4 unreachable, by design)
```

The exit code is `0` only when no case fails; live cases against an
unreachable upstream count as `skip`, not `fail`.

**Filtering and verbosity**

Positional args select a category (`mq` / `ace` / `cert`) or a tool name
substring; dash-prefixed args control how much of each tool's output is printed
(default: first **12** lines, then a `... (N more lines)` marker):

```powershell
# Only the certificate tool, full untruncated output
.venv\Scripts\python.exe clients\smoke_test.py cert --full

# First 30 lines of each ACE-tool result
.venv\Scripts\python.exe clients\smoke_test.py ace --lines=30
```

| Flag | Effect |
| --- | --- |
| `--full` / `-f` | Print every line of each tool's output (no truncation). |
| `--lines=N` | Print the first `N` lines of each output. |
| _(none)_ | Default — first 12 lines per output. |

**Output format**

Each case prints its number, tool name, args, an output preview, and the
classified outcome. The run ends with a column-aligned summary table:

```
  #   Tool                   Kind     Result  Mode                   Reason
 ---  ---------------------- -------- ------  ---------------------- ------
   1  mq_queue_inspect       online   pass    live
   4  mq_queue_inspect       offline  pass    expect_not_found
  11  mq_host_overview       online   pass    live
  18  mq_host_overview       offline  pass    expect_blocked
  22  ace_node_overview      online   skip    live                   upstream JSON status=error
  ...
```

- **Kind** — `online` (the case expects to reach an upstream) or `offline`
  (CSV reads or a safety rail that fires before any HTTP).
- **Result** — `pass` / `skip` / `fail`.
- **Mode** — the case's tag in `CALLS`. See "Mode reference" below.

**Mode reference**

| Mode | Pass condition |
| --- | --- |
| `live` | output is not a sanitised `⚠️/❌/🚫` envelope; upstream-unreachable → `skip` |
| `offline` | output is not an error envelope; CSV-backed cases |
| `expect_not_found` | output starts with `❌` and contains "not found" |
| `expect_blocked` | output contains the `MODIFY_BLOCKED_MSG` banner |
| `expect_warn_no_qmgr` | output contains "without \`qmgr_name\`" |
| `expect_error_envelope` | top-level `{"status":"error"}`, or `⚠️/❌` prefix, or any `*_error` key in the JSON envelope |

**The 44 cases at a glance**

| Tool | # cases | Online | Offline | Coverage |
| --- | --- | --- | --- | --- |
| `mq_queue_inspect` | 6 | 5 | 1 | discovery, FAST PATH, **multi-target (two queues, one call)**, alias resolution, remote-queue routing (QR.IN.APP2 → RQMNAME/RNAME/XMITQ), sanitised "not found" |
| `mq_channel_inspect` | 4 | 3 | 1 | discovery, FAST PATH, **multi-target (two channels, one call)**, sanitised "not found" |
| `mq_host_overview` | 14 | 12 | 2 | default URL, manifest-resolved host, **multi-target (two QMs, one call)**, `DISPLAY QMGR ALL`, full queue properties, max depth + thresholds, queue creation date (`CRDATE CRTIME`), focused QMGR properties, topics, topic status, subscriptions, subscription status, `MODIFY_BLOCKED_MSG` over the wire, "without `qmgr_name`" warning |
| `ace_node_overview` | 5 | 4 | 1 | NODE2 live, **multi-target (two nodes, one call)**, NODE3 (empty servers edge), NODE4 (unreachable → skip), ghost-node graceful envelope |
| `ace_server_explore` | 6 | 5 | 1 | NODE2/IS001, **multi-target (IS001+IS002, one call)**, NODE2/IS002, NODE2/snaps, NODE2/IS001/snaplogic1 (scoped), ghost-server graceful envelope |
| `ace_search` | 5 | 0 | 5 | nodes scope, dump scope, **multi-target (match either, one call)**, default `all` scope, invalid scope (`ace_search` is by design CSV-only — no live path) |
| `get_cert_details` | 4 | 0 | 4 | match by hostname, match by alias, **multi-target (two queries merged, one call)**, no-match empty-results (CSV-only — no live path) |
| **Total** | **44** | **29** | **15** | |

**Adding or editing cases**

Cases live in the `CALLS` list at the top of `clients/smoke_test.py`,
grouped by tool with `# --- toolname ---` section comments. Each entry is
`(tool_name, args_dict, mode_tag)`. To add an expectation that no existing
mode covers, add a branch to `classify()` further down the same file.

## Manifests are shared

CSV manifests (`qmgr_dump.csv`, `node_dump.csv`, `node_config.csv`,
`cert_dump.csv`) are read from `../resources/` by default — the same files the
root server reads. If your deployment puts them elsewhere, set `RESOURCES_DIR`
(or the individual `MQ_QMGR_DUMP_PATH` / `ACE_NODE_*_PATH` / `CERT_DUMP_PATH`)
in `.env`.

### Auto-reload (no restart)

The manifests are replaced by a daily extract job. Each loader goes through
`server/csv_cache.py:CsvCache`, which checks the file's `(mtime, size)` on every
access and **reloads only when it changed** — so the daily swap is reflected on
the next tool call **without restarting the server** (one `os.stat` per access;
re-parsed only when the file actually changes). A read that lands mid-write keeps
serving the previously-loaded data and retries next call. `GET /healthz` exposes
per-manifest freshness under `"manifests"` (`rows`, `file_mtime`, `loaded_at`,
`stale`).

> **Ops note:** have the extract job write a temp file and **atomically rename**
> it into place (e.g. `os.replace`) so readers never see a half-written CSV.

## Logs are NOT shared

Application logs and the per-call query log default to `../logs-single/`
(separate from the root server's `logs/`) so Power BI ingests them as their
own dataset. Override with `LOG_DIR` in `.env`.
