# MQ & ACE Chatbot — 33 Test Questions

Derived from **qmgr_dump.csv** (Queue Manager `MQQMGR2` on `lopalhost`), **node_dump.csv** (4 ACE integration nodes across `lodace01`/`loqace02`/`lotace03`/`loqace04.example.com`), and **cert_dump.csv** (TLS/SSL certificate inventory).

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

### Integration Node & Server Status

**Q16 — Integration node overview**
> "List all integration nodes and their host machines."

*Expected answer area:* `NODE01` on `lodace01.example.com`, `NODE02` on `loqace02.example.com`, `NODE03` on `lotace03.example.com`, `NODE04` on `loqace04.example.com`.

---

**Q17 — Stopped integration servers**
> "Which integration servers are currently stopped across all nodes?"

*Expected answer area:*
- `IS003` on `NODE01` (lodace01.example.com) — stopped
- `IS012` on `NODE02` (loqace02.example.com) — stopped
- `IS031` on `NODE04` (loqace04.example.com) — stopped

---

**Q18 — Server status on a specific node**
> "What is the status of all integration servers on NODE03?"

*Expected answer area:* `IS020` — running, `IS021` — running, `IS022` — running. All three servers on NODE03 are up.

---

**Q19 — Single server status**
> "Is integration server IS011 on NODE02 running?"

*Expected answer area:* Yes — `IS011` on `NODE02` (loqace02.example.com) is running with application `fraud_detection` deployed.

---

### Applications & Message Flows

**Q20 — Applications deployed on a server**
> "What application is deployed on integration server IS020, and what message flows does it contain?"

*Expected answer area:* Application `shipping_app` is deployed. It has two flows: `ShipmentCreateFlow` (running) and `ShipmentNotifyFlow` (running).

---

**Q21 — Stopped or inactive message flows**
> "Which message flows are not in a running state? Include their application, server, and node."

*Expected answer area:*
- `InvoiceFlow` (snaplogic1 / IS001 / NODE01) — stopped
- `StockUpdateFlow` (warehouse_app / IS021 / NODE03) — stopped
- `InventoryPushFlow` (inventory_sync / IS022 / NODE03) — inactive
- `InvoiceFlow` (snaplogic1 / IS0033 / NODE04) — stopped

---

**Q22 — Application spanning multiple nodes**
> "On which nodes and servers is the application 'snaplogic1' deployed?"

*Expected answer area:*
- `IS001` on `NODE01` (lodace01.example.com) — OrderFlow running, InvoiceFlow stopped
- `IS001` on `NODE02` (loqace02.example.com) — main flow running
- `IS0033` on `NODE04` (loqace04.example.com) — OrderFlow running, InvoiceFlow stopped

---

**Q23 — Specific flow status**
> "What is the status of the FraudCheckFlow message flow?"

*Expected answer area:* `FraudCheckFlow` in application `fraud_detection` on `IS011` / `NODE02` (loqace02.example.com) is **running**.

---

**Q24 — Notification application flows**
> "What message flows are deployed under the notification_app application, and are they running?"

*Expected answer area:* `notification_app` is on `IS032` / `NODE04` (loqace04.example.com). Flows: `EmailNotifyFlow` — running, `SMSNotifyFlow` — running. Both are active.

---

**Q25 — Inactive flow investigation**
> "The InventoryPushFlow is inactive. Which server and node is it on, and what application does it belong to?"

*Expected answer area:* `InventoryPushFlow` belongs to `inventory_sync`, deployed on `IS022` which is running on `NODE03` (lotace03.example.com). The flow status is **inactive** — the server is up but the flow itself has been individually deactivated.

---

**Q26 — BIP message code lookup**
> "What does BIP1288I mean in the context of these ACE logs?"

*Expected answer area:* `BIP1288I` indicates the status of a **message flow** — specifically reporting whether a named flow in a given application on a given integration server is running, stopped, or inactive. Example: `BIP1288I: Message flow 'OrderFlow' in application 'snaplogic1' on integration server 'IS001' is running.`

---

**Q27 — Node-level summary**
> "Give me a health summary for NODE04."

*Expected answer area:*
- `IS030` — running; `customer_app` / `CustomerCreateFlow` running
- `IS031` — **stopped** (no applications reported)
- `IS032` — running; `notification_app` / `EmailNotifyFlow` running, `SMSNotifyFlow` running
- `IS0033` — running; `snaplogic1` / `OrderFlow` running, `InvoiceFlow` **stopped**

---

**Q28 — Flow count per node**
> "How many message flows are running on loqace02.example.com?"

*Expected answer area:* 2 running flows on NODE02 (loqace02.example.com): `main` (snaplogic1 / IS001) and `FraudCheckFlow` (fraud_detection / IS011).

---

### Cross-System (MQ + ACE)

**Q29 — MQ queue used by ACE application**
> "The billing_app on ACE writes to QL.IN.APP1 on MQQMGR2. Is that queue accepting messages, and are there any depth alerts configured?"

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
