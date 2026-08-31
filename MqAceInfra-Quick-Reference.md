# MqAceInfra — Quick Reference

_Companion to the MqAceInfra announcement. Attach or link this alongside the email._

## What it can do

### IBM MQ — queues and channels

- **Any queue property, in one question.** Current and maximum depth, persistence, maximum message length, default priority, get/put enabled, triggering, backout threshold and backout queue, creation and last-altered timestamps. Alias queues are followed through to the underlying local queue automatically, and remote queues come back with their full routing (remote queue, remote queue manager, transmission queue).
- **Any channel — status and configuration together.** Whether it is running, what it connects to, its TLS cipher, peer name and certificate label, batch size, heartbeat interval and maximum message length. One question returns both the runtime state and the definition.
- **You do not need to know where the object lives.** Name the queue or channel and the assistant finds every queue manager hosting it. If you name a queue manager and the object is not actually there, it tells you where it really is.

### IBM MQ — host and estate level

- **Host overview** — which queue managers run on a host, and the installed MQ version and build.
- **Queue manager runtime state** — running or not, start and restart time, channel initiator and command server state, connection count.
- **Any read-only MQSC `DISPLAY` command** you would normally run at a command line — listeners, cluster membership, queue manager attributes, and the rest.
- **Estate-wide sweeps.** Ask a question without naming a queue manager and every queue manager in the inventory is checked. "Does every queue manager have a dead-letter queue set?" is a single question, not a spreadsheet exercise.

### IBM ACE — nodes, servers, applications, flows

- **Node overview** — node status, version, and every integration server on it with its running state and properties: trace levels, debug port, JVM heap, HTTP/HTTPS connector ports, monitoring and exception-logging settings, default queue manager.
- **Integration server contents** — the applications deployed to a server and the message flows inside them, with running or stopped state per flow.
- **Resource-manager configuration** — roughly 35 resource managers per integration server, including the global cache (and whether caching is actually switched on), the XPath cache, JVM manager, Kafka, MQ connection manager, HTTP and HTTPS connectors, database and Redis connections, decision-server integration, OpenTelemetry, activity log and Node.js. Each one is reported both as **configured** (what the configuration file says) and **active** (what the running server is actually using), so a change still waiting on a restart shows up as a difference between the two.
- **Estate-wide search** — find an application, a message flow, a deployed artefact, a policy, or a diagnostic (BIP) message anywhere across all configured nodes, without knowing which node or integration server it sits on.

### Certificates

- **TLS/SSL inventory lookup** by hostname, alias, or common name (CN). Returns the CN, the alias, the validity window, the exact number of days until expiry (negative if it has already expired), and which ACE integration nodes run on that host.

### Error fact-checking

- **Paste an MQ or ACE connection error and ask "are these details right?"** The assistant pulls the queue manager, host, port and channel out of the error text and checks each one against the authoritative inventory, reporting every field as correct, mismatched, or not found — with the value it should be. This works **even when the endpoint is down**, which is exactly when you need it.

---

## What it will not do — by design

| Guarantee | What it means for you |
| --- | --- |
| **Read-only, enforced at the server** | Commands that alter, define, delete, clear, set, reset, start, stop, purge or refresh anything are blocked before they are ever sent. There is no way to make a change through this assistant, deliberately or accidentally. |
| **Restricted host coverage** | Only hosts on an approved list can be reached. Production sits outside that list by default. |
| **No live certificate probing** | Certificate answers come from the inventory extract, not from opening a connection to the endpoint. |
| **Some answers come from a periodic extract** | Connection fact-checks, ACE search and certificate lookups read a scheduled extract rather than the live system. That is why they still work when a system is unreachable — but they reflect the last extract, not this second. Queue, channel, host, node, integration-server and resource-manager answers are **live**. |
| **It is an assistant, not an authority** | Treat answers as a fast, accurate starting point. Confirm before acting on anything that would drive a change. |

Every question and answer is logged for support and usage reporting. **Please do not paste passwords, API keys, tokens or certificates into the chat** — the assistant never needs them.

---

## Best practices — how to get good answers

1. **Ask one thing per message.** The assistant answers each question with a single lookup. Two unrelated questions in one message means one of them gets a thinner answer. Send them separately.
2. **But batch objects of the same kind.** "What is the depth of `<QUEUE_A>` and `<QUEUE_B>`?" is one question and is answered in one lookup. The same applies to several channels, several nodes, or several certificates.
3. **Do not name the queue manager or node unless you are certain.** The assistant discovers location on its own and will tell you every place an object lives. Supplying the wrong one narrows the search, and you get "not found" instead of your answer.
4. **Say what you want to know, not how to get it.** "Is the global cache on for `<EG>`?" works better than "run a resource-manager query". Describe the outcome; the routing is the assistant's job.
5. **Paste the whole error.** For any connection problem, paste the raw error text and ask whether the details are correct. Do not summarise it — the assistant reads the codes, the connection name and the port out of the original text.
6. **Be exact with names.** Queue, channel, application and integration-server names are matched precisely. If you are unsure of a spelling, ask it to search for the fragment instead and it will offer close matches.
7. **Use follow-ups.** The assistant remembers the conversation, so "and its channels?" after a queue manager question works. When you move to an unrelated topic, use the reset (↺) button to start clean — stale context is the most common cause of an odd answer.
8. **Know whether you need "right now" or "as of the last extract".** For anything time-sensitive — current depth, is it running, is the connection up — ask about the live object (queue, channel, host, node, integration server). Inventory-style questions (certificates, historical diagnostics, connection details) come from the extract.
9. **Ask "which" and "all" questions — they are supported.** "Which integration servers have Kafka configured?", "Which queue managers are missing a dead-letter queue?", "Which certificates expire in the next 30 days?" are each a single question across the whole estate.
10. **When an answer looks wrong, quote the reference.** Errors come back with a short `ref` identifier. Include it when you report a problem — it points straight at the server-side log entry for that exact call.

---

## Sample questions to try

Replace the placeholders with names from your own area.

### Queue managers and hosts

- What is the status of queue manager `<QMGR>`?
- When was queue manager `<QMGR>` last restarted?
- Which queue managers are running on host `<HOST>`, and what MQ version is installed?
- Does every queue manager have a dead-letter queue configured?
- What listener port is each queue manager using?

### Queues

- What is the current depth of `<QUEUE>`?
- What are the depths of `<QUEUE_A>` and `<QUEUE_B>`?
- Is `<QUEUE>` persistent, and what is its maximum message length?
- What does alias queue `<ALIAS_QUEUE>` point to?
- Where do messages put to remote queue `<REMOTE_QUEUE>` actually go?
- When was `<QUEUE>` created, and when was it last altered?

### Channels and channel TLS

- Is channel `<CHANNEL>` running?
- Are `<CHANNEL_A>` and `<CHANNEL_B>` both up?
- What TLS cipher and certificate label are set on `<CHANNEL>`?
- What connection name does `<CHANNEL>` point at, and what is its batch size?

### ACE nodes and integration servers

- What is on integration node `<NODE>`?
- Which integration servers are stopped across all nodes?
- Is service trace or user trace enabled anywhere?
- What JVM heap and HTTP connector ports are set on `<NODE>`?

### ACE applications, flows and diagnostics

- Which applications are deployed to integration server `<EG>`?
- Which message flows are in application `<APPLICATION>`, and are they all running?
- Where is application `<APPLICATION>` deployed? (no node name needed)
- Show me any diagnostic messages mentioning `<BIP_CODE_OR_ERROR_TEXT>`.
- Find anything referencing `<FLOW_A>` or `<FLOW_B>`.

### ACE resource managers

- Is the global cache enabled on `<EG>`?
- List every integration server on `<NODE>` that has the global cache enabled.
- Which integration servers have Kafka configured?

### Certificates

- When does the certificate on `<HOST>` expire?
- Which certificates expire in the next 30 days?
- What is the CN and alias of the certificate used by `<SERVICE_OR_HOST>`?

### Fact-checking an error

- I am getting this when connecting to `<QMGR>` — are the details right? *(paste the full error)*
- Our ACE admin REST call to `<NODE>` on port `<PORT>` fails — is that host and port correct?

---

## Where to start, by role

| If you are… | Start with | What you get out of it |
| --- | --- | --- |
| **MQ / ACE support engineer** | "Is channel `<CHANNEL>` running, and what is its TLS configuration?" | Runtime state and definition in one answer — replaces a login, a command session and two commands. |
| **Application / development team** | "What is the depth of `<QUEUE>`, and is it persistent?" | Self-service answers about the queues and flows your application uses, with no MQ access of your own. |
| **SRE / platform** | "Which integration servers are stopped across all nodes?" or "Does every queue manager have a dead-letter queue?" | Estate-wide posture and drift checks in one question instead of a host-by-host sweep. |
| **QA / test** | "Where is application `<APPLICATION>` deployed, and are its flows running?" | Confirm the environment is in the shape your test expects before you raise a defect. |
| **Managers / leads** | "Which certificates expire in the next 30 days?" | Risk and readiness answers without waiting for someone to compile them. |

---

**Questions or problems:** contact <<SUPPORT_CONTACT>> and include the `ref` identifier from any error message.
