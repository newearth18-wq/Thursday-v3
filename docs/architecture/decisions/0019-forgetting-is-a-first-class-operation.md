# ADR 0019 — Forgetting is an instruction, not a database chore

**Status:** accepted · **Date:** 2026-09-02

## Context

"อย่าจำเรื่องนี้" and "ลืมข้อมูลเรื่อง X" parsed to nothing. They fell through to *I did
not understand*, which is the worst possible answer: the owner has asked for something
privacy-shaped, been given a shrug, and has no way to tell whether it happened.

The spec gives explicit memory commands high priority. The reason is asymmetry — a memory
wrongly kept is a privacy failure the owner cannot see, while a memory wrongly dropped is
an inconvenience they will notice immediately.

## Decision

`MEMORY_FORGET`, handled ahead of the write policy, with two distinct modes.

**Forget X** deletes what is stored. Matching is the maximum of embedding similarity and
literal trigram overlap, at a threshold deliberately higher than a recall's — retrieving
something marginally relevant costs nothing, and deleting it cannot be undone. The lexical
half matters more than it looks: it is the *stronger* signal for deletion ("forget the
budget" means forget things that say so), and it keeps this working on the offline
embedder, whose paraphrase similarity is weak. Without it, "forget X" would quietly match
nothing whenever Thursday is offline — silent failure, on a privacy operation.

**Don't remember this** does two things, because only one would be a half-measure: it stops
future implicit writes from the conversation, *and* removes what the conversation already
wrote. Stopping alone would leave the thing the owner was pointing at still in memory,
which is the opposite of what they asked. This is why `MemoryRecord` carries a
`session_id`: "this" means the exchange that just happened, and something has to be able to
find it.

Both reply with what actually happened, including the count. "Forgotten" alone leaves the
owner unable to distinguish a successful deletion from a search that matched nothing, and
those need different follow-ups.

Suppression is **not** a standing gag. A later explicit "จำไว้ว่า X" in the same
conversation is honoured, because "don't remember this" was about what had just been said.
Reading it as a setting would mean silently ignoring the clearest instruction an owner can
give — the same failure as remembering what they asked to forget, in reverse.

## Consequences

- "Forget it" is refused. It is a figure of speech far more often than an instruction to
  erase memory, and deleting on that reading is not a mistake that can be undone.
- Deletion is bounded (50 records) and returns what it removed, so an over-wide match is
  visible in the reply rather than discovered later.
- **Cost we accepted:** trigram matching is script-agnostic but crude, and will occasionally
  match a record that merely shares vocabulary. The reply names what went, which is the
  mitigation; the alternative — matching too little — fails silently, and silence is the
  one outcome a privacy operation must not have.

## Alternatives considered

- **A `/memory` UI and nothing conversational.** The UI exists and is not enough: the moment
  someone wants something forgotten is the moment they are talking, not browsing.
- **Soft-delete with a tombstone.** Rejected for owner-requested deletion. "Forget that"
  should mean gone, and a system that keeps a copy has not done what it said it did.
- **Suppress the whole session, permanently.** Rejected: see the standing-gag argument.
