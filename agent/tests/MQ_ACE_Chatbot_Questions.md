# MQ & ACE Chatbot — 33 Test Questions

Derived from **qmgr_dump.csv** (Queue Manager `MQQMGR2` on `lopalhost`), **node_dump.csv** (2 ACE integration nodes, `NODE1` and `NODE2`, both on `localhost`), and **cert_dump.csv** (TLS/SSL certificate inventory).

---

## IBM MQ Questions (1–15)

### Queue Manager & Configuration

**Q1 — Queue Manager status**
> "What is the status of queue manager MQQMGR2?"

*Expected answer area:* The chatbot should report MQQMGR2's running status, host (`lopalhost`), and key properties such as CHLAUTH being ENABLED and CONNAUTH set to `SYSTEM.DEFAULT.AUTHINFO.IDPWOS`.

---

**Q2 — Maximum message length**
> "What is the maximum message length configured on MQQMGR2?"

*Expected answer area:* `MAXMSGL(4194304)` — 4 MB, which is also inherited as the default by most queues on this queue manager.

---

**Q3 — Authentication configuration**
> "How is connection authentication configured on MQQMGR2? Is CHLAUTH enabled?"

*Expected answer area:* `CONNAUTH(SYSTEM.DEFAULT.AUTHINFO.IDPWOS)` — password-based OS authentication. `CHLAUTH(ENABLED)` — channel authentication records are active.

---

**Q4 — SSL/TLS key repository**
> "Where is the SSL key repository located for MQQMGR2?"

*Expected answer area:* `SSLKEYR('C:\ProgramData\IBM\MQ\qmgrs\MQQMGR2\ssl\key')` with certificate label `ibmwebspheremqmqqmgr2`.

---

**Q5 — Publish/Subscribe mode**
> "Is Publish/Subscribe enabled on MQQMGR2? What pub/sub queues are configured?"

*Expected answer area:* `PSMODE(ENABLED)` and `PSCLUS(ENABLED)`. System pub/sub queues include `SYSTEM.BROKER.DEFAULT.STREAM`, `SYSTEM.BROKER.ADMIN.STREAM`, `SYSTEM.BROKER.CONTROL.QUEUE`, `SYSTEM.INTER.QMGR.PUBS`, etc. The `SYSTEM.QPUBSUB.QUEUE.NAMELIST` namelist points to the broker streams.

---

### Queues

**Q6 — Application local queue details**
> "Tell me about the queue QL.IN.APP1 on MQQMGR2. What is its max depth and trigger configuration?"

*Expected answer area:* `MAXDEPTH(5000)`, `GET(ENABLED)`, `PUT(ENABLED)`, `NOTRIGGER`, `DEFPSIST(NO)`, `USAGE(NORMAL)`. No trigger is set on this queue.

---

**Q7 — Remote queue routing**
> "How does the remote queue QR.IN.APP2 route messages? Which queue manager and queue does it point to?"

*Expected answer area:* `RQMNAME(MQQMGR1)`, `RNAME(QA.IN.APP2)`, `XMITQ(XMIT.Q.QM2)`. Messages put to `QR.IN.APP2` are forwarded via the transmission queue `XMIT.Q.QM2` to queue `QA.IN.APP2` on `MQQMGR1`.

---

**Q8 — Transmission queue trigger**
> "What type of trigger is configured on the transmission queue XMIT.Q.QM2?"

*Expected answer area:* `TRIGGER`, `TRIGTYPE(FIRST)`, `USAGE(XMITQ)`, `DISTL(YES)`. It is triggered on the first message and serves as the transmission queue for the sender channel.

---

**Q9 — Dead letter queue**
> "What is the dead letter queue on MQQMGR2 and what are its key settings?"

*Expected answer area:* `SYSTEM.DEAD.LETTER.QUEUE` — `MAXDEPTH(999999999)`, `MAXMSGL(4194304)`, `GET(ENABLED)`, `PUT(ENABLED)`, `DEFPSIST(NO)`, `USAGE(NORMAL)`. Note: the QMGR-level `DEADQ` attribute is currently blank (no DLQ assigned to the queue manager by default).

---

**Q10 — Model queues for JMS**
> "What is the maximum message size of the SYSTEM.JMS.TEMPQ.MODEL queue on MQQMGR2?"

*Expected answer area:* `SYSTEM.JMS.TEMPQ.MODEL`, `DEFTYPE(TEMPDYN)`, `MAXMSGL(104857600)` — 100 MB, larger than the default to accommodate JMS payloads.

---

### Channels

**Q11 — Sender channel details**
> "What channel connects MQQMGR2 to MQQMGR1? What is the connection name and transport type?"

*Expected answer area:* Channel `MQQMGR2.TO.MQQMGR1`, `CHLTYPE(SDR)`, `CONNAME('localhost(1414)')`, `TRPTYPE(TCP)`, `XMITQ(XMIT.Q.QM2)`, `BATCHSZ(50)`, `HBINT(300)`.

---

**Q12 — Server-connection channel**
> "What server-connection channel is defined on MQQMGR2 and what are its instance limits?"

*Expected answer area:* `SYSTEM.AUTO.SVRCONN`, `CHLTYPE(SVRCONN)`, `MAXINST(999999999)`, `MAXINSTC(999999999)`, `SHARECNV(10)`, `SSLCAUTH(REQUIRED)` — SSL client authentication is required.

---

**Q13 — AMQP channel**
> "Is there an AMQP channel defined on MQQMGR2? What port does it use?"

*Expected answer area:* `SYSTEM.DEF.AMQP`, `CHLTYPE(AMQP)`, `PORT(5672)`, `SSLCAUTH(REQUIRED)`. It uses the topic root `SYSTEM.BASE.TOPIC` and temp queue prefix `AMQP.*`.

---

### Monitoring & Security

**Q14 — Queue depth event thresholds**
> "What are the queue depth high and low thresholds set on QL.IN.APP1?"

*Expected answer area:* `QDEPTHHI(80)` — alert when depth reaches 80% of `MAXDEPTH`, `QDEPTHLO(20)` — alert when it drops to 20%. Both depth high and low events (`QDPHIEV`, `QDPLOEV`) are currently `DISABLED`; only `QDPMAXEV(ENABLED)` fires when the queue is full.

---

**Q15 — Accounting and statistics**
> "Are accounting and statistics collection enabled on MQQMGR2?"

*Expected answer area:* Queue manager level: `ACCTMQI(OFF)`, `ACCTQ(OFF)`, `STATMQI(OFF)`, `STATQ(OFF)` — both accounting and statistics are off at the QMGR level. Individual queues inherit `ACCTQ(QMGR)` and `STATQ(QMGR)`, meaning they follow the QMGR setting, so no data is currently being collected.

---

## IBM ACE Questions (16–30)

Regrounded on the manifests that actually ship — `resources/node_config.csv`
(NODE1 → localhost:4414, NODE2 → localhost:4415) and `resources/node_dump.csv`
(extract `2026-06-25 10:30:19`, 4 integration servers and 16 applications per
node, all running; the single stopped object is the `CreateItem` flow).

These questions are answerable from the offline extract, so they do not need a
live ACE runtime. `*Expected tools:*` lists `ace_search` alongside the live tool
the routing table would also accept, so a correct answer is not punished for
taking either path.

### Integration Node & Server Status

**Q16 — Integration nodes and their endpoints**
> "Which integration nodes are configured, and on which host and admin REST port does each one run?"

*Expected answer area:* `NODE1` on `localhost` port `4414` and `NODE2` on `localhost` port `4415`, from `node_config.csv`.

*Expected tools:* ace_search
*Must mention:* NODE1, NODE2, localhost, 4414, 4415

---

**Q17 — Stopped integration servers**
> "In the latest ACE extract, are any integration servers reported as stopped across NODE1 and NODE2?"

*Expected answer area:* None. All four integration servers — `ACE_DEMO_CACHE`, `ACE_DEMO_CONNECTORS`, `ACE_DEMO_MESSAGING`, `ACE_DEMO_TRANSFORM` — are reported running (`BIP1286I`) on both nodes. A clean negative result, not an apology for finding nothing.

*Expected tools:* ace_search, ace_node_overview
*Must mention:* NODE1, NODE2, running

---

**Q18 — Server status on a specific node**
> "What is the status of all integration servers on NODE2?"

*Expected answer area:* Four servers, all running: `ACE_DEMO_CACHE`, `ACE_DEMO_CONNECTORS`, `ACE_DEMO_MESSAGING`, `ACE_DEMO_TRANSFORM`.

*Expected tools:* ace_search, ace_node_overview
*Must mention:* ACE_DEMO_CACHE, ACE_DEMO_CONNECTORS, ACE_DEMO_MESSAGING, ACE_DEMO_TRANSFORM, NODE2

---

**Q19 — Single server status**
> "Is the integration server ACE_DEMO_MESSAGING on NODE2 running, and what is deployed on it?"

*Expected answer area:* Yes — running, with five applications: `ACE_message_Grouping`, `ACE_MQ_group_messages`, `ACE_MQ_Syncronus_processing`, `ACE_multi_dest_mq`, `IBMACEJMSInput`, all running.

*Expected tools:* ace_search, ace_node_overview, ace_server_explore
*Must mention:* ACE_DEMO_MESSAGING, NODE2, ACE_MQ_group_messages, IBMACEJMSInput, ACE_multi_dest_mq

---

### Applications & Message Flows

**Q20 — Applications deployed on a server**
> "Search the ACE dump for ACE_DEMO_CACHE and tell me which applications and message flows it reports on NODE1."

*Expected answer area:* Two applications, both running — `ACE_flow_Cache` with flow `akp.Flow_Cache`, and `ACE_add_global_Cache` with flow `global_cache`. Both flows running. Flow names live only in the extract (the live `/messageflows` endpoint is not answering), so this has to go through `ace_search`.

*Expected tools:* ace_search
*Must mention:* ACE_flow_Cache, ACE_add_global_Cache, akp.Flow_Cache, global_cache

---

**Q21 — Stopped or inactive message flows**
> "Which message flows are not in a running state? Include their application, integration server and node."

*Expected answer area:* Exactly one flow, on both nodes: `CreateItem` in application `AmazonS3` on integration server `ACE_DEMO_CONNECTORS` — stopped on `NODE1` and on `NODE2` (`BIP1278I`). Everything else in the extract is running.

*Expected tools:* ace_search
*Must mention:* CreateItem, AmazonS3, ACE_DEMO_CONNECTORS, NODE1, NODE2, stopped

---

**Q22 — Application spanning multiple nodes**
> "On which nodes and integration servers is the application ACE_csv2csv deployed?"

*Expected answer area:* `ACE_csv2csv` runs on integration server `ACE_DEMO_TRANSFORM` on both `NODE1` and `NODE2`, running in both places.

*Expected tools:* ace_search
*Must mention:* ACE_csv2csv, ACE_DEMO_TRANSFORM, NODE1, NODE2

---

**Q23 — Specific flow status**
> "What is the status of the create_group message flow?"

*Expected answer area:* `create_group` belongs to application `ACE_MQ_group_messages` on integration server `ACE_DEMO_MESSAGING`, and is running on both `NODE1` and `NODE2` (`BIP1277I`).

*Expected tools:* ace_search
*Must mention:* create_group, ACE_MQ_group_messages, ACE_DEMO_MESSAGING, running

---

**Q24 — Flows under one application**
> "Find everything in the ACE dump mentioning ACE_MQ_group_messages on NODE1 — which message flows does it report, and are they running?"

*Expected answer area:* Two flows on `ACE_DEMO_MESSAGING`: `read_group` and `create_group`, both running. The node is named in the question, so no clarifying question is warranted.

*Expected tools:* ace_search
*Must mention:* read_group, create_group, ACE_MQ_group_messages, running

---

**Q25 — Stopped-flow investigation**
> "The CreateItem flow is not running. Which integration server and node is it on, what application does it belong to, and does the dump also show that ACE_DEMO_CONNECTORS server itself as running?"

*Expected answer area:* `CreateItem` belongs to `AmazonS3`, deployed on `ACE_DEMO_CONNECTORS`, on both `NODE1` and `NODE2`. The integration server itself is **running** — it is the flow alone that is stopped, so this is a flow-level stop, not a server outage.

*Expected tools:* ace_search
*Must mention:* CreateItem, AmazonS3, ACE_DEMO_CONNECTORS, stopped, running

---

### ACE BIP codes & summaries

**Q26 — BIP message code lookup**
> "What do BIP1277I and BIP1278I mean, and which objects in this environment report each one?"

*Expected answer area:* `BIP1277I` reports a message flow **running**; `BIP1278I` reports a message flow **stopped**. In this extract every flow reports `BIP1277I` except `CreateItem` (application `AmazonS3`, server `ACE_DEMO_CONNECTORS`), which reports `BIP1278I` on both nodes.

*Expected tools:* ace_search
*Must mention:* BIP1277I, BIP1278I, CreateItem, stopped

---

**Q27 — Node-level health summary**
> "Search the ACE dump for NODE1 and summarise what it reports — integration servers, applications, and any message flow that is not running."

*Expected answer area:* `NODE1` on `localhost:4414`. Four integration servers, all running. Sixteen applications, all running. Eighteen message flows running and one stopped: `CreateItem` in `AmazonS3` on `ACE_DEMO_CONNECTORS`.

*Expected tools:* ace_search
*Must mention:* NODE1, ACE_DEMO_CACHE, ACE_DEMO_CONNECTORS, ACE_DEMO_MESSAGING, ACE_DEMO_TRANSFORM, CreateItem

---

**Q28 — Flow count for a node**
> "Find all BIP1277I and BIP1278I entries in the ACE dump for NODE1 — how many message flows are running and how many are not?"

*Expected answer area:* 18 running flows (`BIP1277I`) and 1 not running (`BIP1278I` — `CreateItem`), for 19 flows total on `NODE1`. `ace_node_overview` carries no flow data, so only the dump can answer this.

*Expected tools:* ace_search
*Must mention:* NODE1, CreateItem

---

### Cross-System (MQ + ACE)

**Q29 — MQ queue used by ACE application**
> "The ACE_multi_dest_mq application on ACE_DEMO_MESSAGING writes to QL.IN.APP1 on MQQMGR2. Is that queue accepting messages, and are there any depth alerts configured?"

*Expected answer area:* `QL.IN.APP1` has `PUT(ENABLED)` and `GET(ENABLED)` — it is open for both put and get operations. `MAXDEPTH(5000)`. Depth alerts: `QDPHIEV(DISABLED)` and `QDPLOEV(DISABLED)`, so no active events fire on depth changes (only max-depth event is enabled).

---

**Q30 — End-to-end message path**
> "Trace the path of a message put to QR.IN.APP2 on MQQMGR2 until it reaches its destination queue."

*Expected answer area:*
1. Application puts message to **QR.IN.APP2** (remote queue on MQQMGR2).
2. MQ resolves it: `RQMNAME(MQQMGR1)`, `RNAME(QA.IN.APP2)`, `XMITQ(XMIT.Q.QM2)`.
3. Message is placed on transmission queue **XMIT.Q.QM2** (`USAGE(XMITQ)`, triggered).
4. Trigger fires the sender channel **MQQMGR2.TO.MQQMGR1** (`CHLTYPE(SDR)`, `CONNAME(localhost(1414))`, `TRPTYPE(TCP)`).
5. Channel transmits the message to **MQQMGR1** on port 1414.
6. Message arrives on destination queue **QA.IN.APP2** on MQQMGR1.

---

## Certificate Questions (31–33)

### TLS/SSL Certificate Inventory

**Q31 — Certificate expiry by host**
> "When does the TLS certificate on lodmq01 expire?"

*Expected answer area:* `get_cert_details("lodmq01")` returns the cert for
`lodmq01.example.com` (alias `mq-ssl-2026`, CN `CN=lodmq01.example.com,…`) with
`valid_from` Mon Jan 12 2026 and `valid_until` (the expiry date) Tue Jan 12
2027. `expirydays` is computed live (days until expiry; negative if expired),
and `ace_nodes` is empty here because `lodmq01` is a pure-MQ host. (For an ACE
host such as `lodace01`, `ace_nodes` lists the node — e.g. `NODE01` — running
there.) Offline inventory — `resources/cert_dump.csv` + `resources/node_dump.csv`.

---

**Q32 — Look up a certificate by alias**
> "Show me the certificate details for alias mqweb-https."

*Expected answer area:* matches `loqmq02.example.com` — the search spans all
columns, so hostname, alias, and CN are all valid lookup keys.

---

**Q33 — Certificates for a domain**
> "Which certificates are issued for example.com?"

*Expected answer area:* a substring search on `example.com` returns every cert
row whose CN/host contains it, each with its validity window and day-count span.

---

## Question Category Summary

| Category | Q# | Count |
|---|---|---|
| QM Configuration (auth, SSL, pub/sub, limits) | 1–5 | 5 |
| Queue types & attributes | 6–10 | 5 |
| Channel types & routing | 11–13 | 3 |
| Monitoring & accounting | 14–15 | 2 |
| ACE node/server status | 16–19 | 4 |
| ACE applications & flows | 20–25 | 6 |
| ACE BIP codes & summaries | 26–28 | 3 |
| Cross-system MQ + ACE | 29–30 | 2 |
| Certificate inventory | 31–33 | 3 |
| **Total** | | **33** |
