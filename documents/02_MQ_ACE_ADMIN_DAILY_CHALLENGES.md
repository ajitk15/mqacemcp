# Slide 02 — A Day in the Life of an MQ/ACE Platform Admin

**Sub-headline:** *Every routine question takes minutes-to-hours, an expert, and a stack of disconnected tools.*

> Voice of the slide: **us — the MQ/ACE Platform Support team.** What our day actually looks like — in language an executive *and* an engineer can both nod at.

---

## The pain — what slows us down every day

- **No single place to look.**
  - The state of our estate lives across **dozens of admin consoles** and ticket queues. There is no one dashboard that tells us "what is the health of everything, right now."
  - To answer one routine question, we typically have **five or more screens open at once**.

- **No consolidated inventory of our own platform.**
  - Knowing *which system holds what* depends on tribal memory and scattered records that no one owns end-to-end.
  - When that picture drifts from reality (and it eventually does), we find out the hard way — by chasing the wrong system first.

- **Simple questions take a long detective trail.**
  - *"Where is this message stuck?"* often means tracing the same message through several layers — partner gateway → integration layer → queue → application — each in a different tool.
  - A question that *should* take seconds takes **20 to 45 minutes** on a good day.

- **We are the bottleneck for every other team.**
  - App teams, support tiers, project leads — they all wait on us to answer *"is it running?"*, *"is it deployed?"*, *"is it backed up?"*.
  - When two of us are heads-down on a real incident, everything else stops.

- **The estate runs 24×7. Our coverage is 16×5.**
  - Nights, weekends, holidays — there's no expert available to answer even basic diagnostic questions.
  - A problem detected at 9 PM Friday waits until Monday morning unless someone gets paged. We *do* get paged — frequently — outside hours, for things that aren't actually emergencies.

- **The skill is rare and slow to build.**
  - It takes **months** for a new joiner to be productive — they shadow senior admins, learning the consoles, the quirks, the "where do I look for this" map.
  - Two or three people hold most of the deep knowledge. If they're on leave or on an incident, the team's capacity drops sharply. This is a real business risk.

- **The same questions come in every single day.**
  - *"What's the depth of this queue?"*, *"Is this app deployed?"*, *"Why is this message late?"* — asked daily, by the same teams, about the same components.
  - **Nothing gets faster the second time** — there's no automation, no shortcut. Each ask consumes the same effort as the first.

- **Different admins can give different answers to the same question.**
  - We each navigate the consoles differently and may reach slightly different conclusions. There is no single, repeatable, auditable source of truth.

- **Audit, compliance, and "who looked at what" is painful to produce.**
  - When risk or audit asks *"prove that no one touched production between dates X and Y"*, the evidence is scattered across personal terminal histories, console logs, and ticket comments.
  - Building that pack is days of work that should be a one-click report.

- **Production changes happen at night and on weekends.**
  - Patches, upgrades, certificate rotations, failover drills — almost all of these land outside business hours by necessity, because the platform must stay up.
  - That tax is paid by the same small team, on top of the day job.

- **Cross-platform complexity multiplies the problem.**
  - The estate spans **mainframe and distributed servers**, on-prem and partner-connected. Each platform has its own commands, its own oddities, its own specialists.
  - Each *additional* system we onboard adds another console, another login, another thing to check.

- **Toil crowds out engineering.**
  - The team that should be doing **capacity planning, automation, modernisation, and resilience testing** is instead spending most of its hours answering routine state questions.
  - The result: every year we fall further behind on the work that would actually reduce the toil. It's a vicious cycle.

- **Tribal knowledge is a single point of failure.**
  - Critical know-how lives in heads, in personal notes, in chat history — not in systems.
  - When a senior admin leaves, a chunk of operational memory walks out with them.

---

## The bottom line — what the business sees

| **20–45 min** | **16×5** | **2–3 people** | **Months** |
|:---:|:---:|:---:|:---:|
| To answer a routine question | Our coverage of a 24×7 platform | Hold most of the deep knowledge | To onboard a new admin |

---

**Speaker note:** Every pain on this slide has the same root cause: the diagnostic experience is **manual, fragmented, and expert-gated**. It's not a staffing problem — adding people doesn't fix it, because the tooling forces the same workflow on every person. We haven't sat still: over the last two years the team has built thirteen in-house automations that take the most painful, repetitive work off our plate — the next slide walks through that journey, and the five tracks they now cover.
