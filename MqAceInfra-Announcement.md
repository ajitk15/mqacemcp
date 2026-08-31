> **Before sending:** replace `<<CHAT_URL>>`, `<<ENVIRONMENT_SCOPE>>`, `<<SUPPORT_CONTACT>>`, `<<YOUR_NAME>>`. Attach or link `MqAceInfra-Quick-Reference.md`. Then delete this box.

---

**Subject:** MqAceInfra — ask questions about our MQ and ACE estate in plain English

Hello all,

**MqAceInfra** is now available: a chat assistant that answers questions about our IBM MQ and IBM App Connect Enterprise (ACE) estate in plain English — no client tools, no MQ or ACE access of your own, no installation.

**Open it here: <<CHAT_URL>>** (covers **<<ENVIRONMENT_SCOPE>>**)

It is **strictly read-only** — it cannot change, start, stop or delete anything. Explore freely.

### What you can ask it

- **MQ queues** — depth, persistence, max message length, triggering, backout, created/last-altered; alias targets and remote-queue routing resolved for you.
- **MQ channels** — running state *and* configuration together, including TLS cipher, peer and certificate label.
- **MQ hosts and estate** — which queue managers run where, versions, runtime state, and any read-only `DISPLAY` command; estate-wide questions like "does every queue manager have a dead-letter queue?" are a single question.
- **ACE** — node and integration-server status and properties, deployed applications and message flows, resource-manager settings (global cache, Kafka, JVM, connectors) with configured-vs-active, and search across all nodes for an application, flow, artefact or BIP message.
- **Certificates** — CN, alias, validity window and exact days to expiry, by host or service name.
- **Error fact-checking** — paste an MQ or ACE connection error and it checks the queue manager, host, port and channel against the authoritative inventory. Works even when the endpoint is down.

You do not need to know which queue manager or node an object lives on — it finds that for you.

### Five things worth knowing

1. **One question per message**, but batch objects of the same kind ("depth of `<QUEUE_A>` and `<QUEUE_B>`" is one question).
2. **Don't guess the queue manager or node** — leave it out and it will tell you where the object actually is.
3. **Paste the full error text** for connection problems; don't summarise it.
4. **Use follow-ups** — it remembers the conversation. Hit reset (↺) when you switch topic.
5. **Never paste passwords, keys or tokens** into the chat. It never needs them, and all questions are logged.

### Try one of these

- What is the current depth of `<QUEUE>`, and is it persistent?
- Is channel `<CHANNEL>` running, and what TLS cipher is set on it?
- Which integration servers are stopped across all nodes?
- Where is application `<APPLICATION>` deployed, and are its flows running?
- Which certificates expire in the next 30 days?

**The attached quick reference** has the full capability list, best practices, and around 30 sample questions grouped by area — including a "where to start" row for support, development, SRE, QA and management.

Please try it on a real question from your current work this week. If something looks wrong, contact <<SUPPORT_CONTACT>> with the `ref` identifier shown in the message — and tell us what you could not get answered, as that is what drives the next set of capabilities.

Thanks,

<<YOUR_NAME>>
