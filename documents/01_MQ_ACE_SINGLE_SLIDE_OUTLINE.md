# Slide — IBM MQ & IBM ACE: The Invisible Backbone of Enterprise Integration

**Sub-headline:** *IBM MQ guarantees the message. IBM ACE transforms it.*

---

## 1. What they are

- **IBM MQ** — Message-oriented middleware. A transport that moves business messages between applications with guaranteed, transactional, once-and-once-only delivery.
  > *MQ guarantees the message arrives, exactly once, even if the other side is down right now.*
- **IBM ACE (App Connect Enterprise)** — An integration runtime. Hosts graphical message flows that consume an input on one protocol, transform/enrich/route it, and produce output on one or more other protocols.
  > *ACE is the place where "JSON-over-HTTPS from a partner portal" turns into "X12 837 claim on an MQ queue", with no app code written.*

## 2. Where they fit in the enterprise landscape

7-layer reference stack (top → bottom):

| Layer | Function | This slide |
|---|---|---|
| 1 — Channel / Experience | Portals, mobile | |
| 2 — Experience APIs / Gateway | OAuth, throttling | |
| 3 — Process / Orchestration | BPM, case mgmt | |
| **4 — Integration** | **IBM ACE** — message flows, mapping, mediation, routing | ◀ |
| **5 — Messaging / Transport** | **IBM MQ** — durable queues, topics, HA cluster | ◀ |
| 6 — Systems of Record | Claims, Policy, Member, Provider, Mainframe | |
| 7 — Data | Warehouse, lake, MDM | |

Together, MQ + ACE form the **ESB (Enterprise Service Bus)** — the bus every claims intake, eligibility check, enrollment feed, and provider data exchange passes through.

## 3. Landscape — key volumetrics (typical carrier estate)

| 150+ | 60 | 200 | 600 | 5,000+ | 8,000+ |
|:---:|:---:|:---:|:---:|:---:|:---:|
| Queue Managers | ACE Integration Nodes | Integration Servers | Deployed Applications | Active Queues | Message Flows |

**Environments:** Development · Integration Test · UAT · Production
**Platforms:** On-prem Linux · AIX · z/OS (mainframe)

## 4. Insurance business domains supported

| # | Domain | Standard / Protocol |
|---|---|---|
| 1 | Claims intake | X12 **837** in / **835**, **277CA** out |
| 2 | Eligibility & benefits | X12 **270 / 271** |
| 3 | Enrollment | X12 **834** |
| 4 | Member & provider portals | REST/JSON ↔ SOAP/MQ → mainframe |
| 5 | Provider data (credentialing, rosters, fees) | Multi-source merge over MQ |
| 6 | Pharmacy / PBM | **NCPDP** |
| 7 | Correspondence / EOB | Print/output, MQ pub/sub fan-out |
| 8 | Reinsurance & TPA feeds | EDI · SFTP · AS2 |

---

**Speaker note:** MQ owns the transport guarantee; ACE owns the translation. The carrier can swap a portal, upgrade a claims engine, or onboard a new partner without touching the rest — because everything meets in the middle on a queue or a flow.
