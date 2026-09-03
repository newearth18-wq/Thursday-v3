# ADR 0038 — A lost charge makes the cap under-bind

**Status:** accepted · **Date:** 2026-09-03 · **Follows:** [ADR 0037](0037-a-missing-audit-entry-leaves-a-valid-chain.md)

## Context

The spend ledger was the last of the three in-process stores. Sprint 45 built the cap and
named the gap in the same breath: with the ledger in memory, a restart reset the daily total,
so restarting was a way around the ceiling. Sprint 47 closed it only for somebody who had
thought to take a backup.

It looked like the same job as memory and audit, and mostly it is. Two things are different
enough to be worth writing down.

## Decision

**Its own table, not a column on `agent_runs`.** That table already carries `tokens_in` and
`cost_usd`, and using it would have been the obvious shortcut. The grain is wrong: the two
model calls every conversational turn makes — the reasoning pass that interprets the utterance
and the supervisor pass that verifies the result — are not agent runs. Recording spend against
agent runs is *precisely* the accounting that reported zero for a day of conversation, which is
what Sprint 45 existed to fix. Re-adopting it here would have quietly undone that.

**A failed write is recorded, not raised.** The model call has already happened and already
cost money. Raising would report an error for something that succeeded and invite a retry that
spends again — the same reasoning as ADR 0037's post-action audit case.

**But what it costs is different, and the difference is the interesting part.** A lost audit
entry is a lost *record*: something happened and there is no account of it. A lost charge is a
lost *constraint* — the cap under-binds after the next restart, and the owner spends more than
they set out to. One is an accountability failure; the other is a live effect on their money.
So `degraded` is not merely logged: it is a health failure, because a ceiling nobody can trust
is a ceiling that is not doing its job, and which kind they have needs to be visible.

The charge stays in memory either way, so the cap keeps binding for the rest of the session.
The failure costs durability, not the ceiling.

**Pruning reaches the table.** `CostMeter` has a 90-day retention window and dropped expired
charges from its list. With storage behind it, pruning only memory leaves the rows to be
reloaded on the next start — so the window never actually applies and the ledger grows for
ever. This is the same shape as a memory dropped from the index and left in the table, which
comes back as though it had never been forgotten (ADR 0019). Deleting spend rows is safe in a
way deleting audit rows is not: the retention is operational and already documented, and
nothing chains onto them.

**`Charge` carries its own id.** The first draft kept a dict mapping charges to row ids so
pruning could delete them — a second structure keyed on "the same charge", which is a second
source of truth waiting to disagree with the first. An id on the record itself removes the
question.

**`import_state` accepts both shapes it is given.** A backup archive is JSON, so its timestamps
and ids are strings; the repository hands back Python objects straight off the table.
Normalising in one place means neither caller has to remember — and a loader that accepted only
one of them would work perfectly until the day the other was used.

**No repository at all when persistence is off**, rather than a `NullRepository`. `CostMeter`
skips the storage path entirely instead of awaiting a no-op on every model call, which is the
hottest of the three paths.

## Consequences

- Restarting is no longer a way around the spending cap, without needing a backup.
- `CostMeter.record` and `ModelRouter._meter` are async. Four test files needed `await`; no
  production call site outside the router touches them.
- All three in-process stores — memory, audit, spend — now report their durability through
  `health()`, so "is this deployment actually keeping anything" has one answer per component
  rather than being inferred from configuration.
- **Cost we accepted:** one round trip per model call. The call itself costs money and takes
  hundreds of milliseconds; the write is not the expensive part.

## Alternatives considered

- **Reuse `agent_runs`.** Rejected — see above. It is the shortcut that reintroduces the bug
  the cap was built to fix.
- **Batch charges and flush periodically.** Rejected: it reopens the window where the ledger
  and the table disagree, and the window is exactly where a crash loses the spending that
  triggered it.
- **Raise on a failed write.** Rejected: the money is already gone, and a reported failure
  invites a retry that spends it again.
- **Let pruning be storage's problem, via a scheduled job.** Rejected as a second mechanism
  with its own failure mode, for a list of a few thousand rows.
