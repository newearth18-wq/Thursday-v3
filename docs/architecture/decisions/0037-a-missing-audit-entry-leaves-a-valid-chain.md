# ADR 0037 — A missing audit entry leaves a valid chain

**Status:** accepted · **Date:** 2026-09-03 · **Follows:** [ADR 0036](0036-the-table-is-the-truth-the-dict-is-an-index.md)

## Context

ADR 0036 persisted memory and named the audit log as the next gap, deferred because
`AuditLog.record` was synchronous and persistence is not. The count turned out to be four
production call sites, all already inside `async def execute`, so making it async was small.

The hard part was never the plumbing. It was deciding what a *failed* audit write means, and
the answer turns on a property that is easy to state and easy to miss:

> `verify_chain` detects an entry that was altered or removed. It cannot detect an entry that
> was **never written** — a missing entry leaves a perfectly valid chain.

The hash chain is the mechanism that makes deletion detectable rather than merely forbidden
(threat T10). It walks entries comparing each `prev_hash` to the one before. An entry that
never existed breaks nothing. So a dropped audit write is invisible to the exact mechanism
that exists to catch tampering — which rules out the reflex of logging the error and carrying
on, because the error would be the only trace, in the log nobody keeps.

## Decision

**`record` is async, persists, and raises when it cannot.** `AuditWriteError` is a distinct
type so a caller can tell "the audit failed" from "the work failed".

**The in-memory append happens first and cannot fail.** The entry is queryable for the rest of
the session whatever storage does. An entry missing from the chain would be undetectable; one
missing from the table is at least visible now, and `degraded` says so afterwards.

**`degraded` never goes green again.** The gap it marks does not heal, and a flag that cleared
on the next success would report a complete log that is permanently missing an entry.

**What a caller does with the raise depends on when it happens, and the call sites say so.**

- *Before* the action has run — a BLOCK, a denied approval — the error propagates. Refusing an
  action Thursday cannot account for costs nothing.
- *After* the tool has run, `execution.py` catches it. Reporting failure for something that
  already happened invites a retry, and §194 forbids silently duplicating an external
  communication: a retried email is a second email. The entry stays in the chain, the log
  marks itself degraded, and health goes red.

The third option — swallowing it silently — is the one the chain cannot cover, and is the
reason this ADR exists.

**A degraded log is a health failure, not a note.** Nothing else reports it, so if health is
not red then nothing says so at all.

**Entries load in written order, with their stored hashes.** Order because `verify_chain`
depends on it and rows in arbitrary order would fail an intact chain. Stored hashes because
re-hashing on load produces a chain that verifies whatever it was handed — the same argument
ADR 0032 made for backups.

**The chain continues across the restart.** An entry written after a restart chains onto the
last entry written before it. Without that, everything before a restart could be deleted with
`verify_chain` none the wiser — which is precisely the deletion it exists to catch.

## Consequences

- "Who did what, on whose behalf, with whose permission" is answerable after a restart, and
  tampering with the stored rows is still detected.
- **A real schema gap closed:** `audit_logs` had no `origin_device_id` column, though V8 added
  the field to `AuditEntry` so a remote command's origin is auditable. Persisting without it
  would have silently dropped the one field that makes a remote command accountable — "who
  told my PC to do that, and from where" is unanswerable from an entry recording only the
  target. Migration `f7c584518f25`.
- **A pre-existing bug fixed in passing:** the health check reported `ok: False` for a broken
  chain next to the literal words "hash chain intact", because the detail string was
  unconditional. Adding a second audit check is what surfaced it — the duplicate shadowed the
  original in a dict keyed by component, and the test caught that too.
- **Cost we accepted:** the table grows without bound. A retention policy is deliberately not
  implemented here: deleting audit rows is what the append-only design forbids, and how long
  the owner keeps their own record is their decision, not a default.

## Alternatives considered

- **Buffer in memory and flush at turn boundaries.** Rejected. It makes durability depend on
  reaching a flush point, so a crash mid-turn loses exactly the entries most likely to matter,
  and "was it recorded" becomes "probably".
- **Fail the action when the audit write fails.** Rejected for post-action records — see
  above. Adopted for pre-action ones, where it is free and correct.
- **Log the error and continue.** Rejected: it is the one failure mode the chain cannot detect.
- **Recompute hashes on load so the chain always verifies.** Rejected, and worth naming
  because it looks like a fix: a chain that always verifies is a chain that proves nothing.
