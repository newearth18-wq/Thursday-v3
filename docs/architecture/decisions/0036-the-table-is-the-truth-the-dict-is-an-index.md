# ADR 0036 — The table is the truth, the dict is an index

**Status:** accepted · **Date:** 2026-09-03

## Context

`docs/23-release-readiness.md` named this the largest remaining gap. The schema was designed,
the migrations were written, `alembic check` ran them from empty on every commit — and nothing
read or wrote through any of it. `db/session.py` existed and had no callers. Restarting
Thursday made it forget everything the owner had told it.

Sprint 47's backup made that *recoverable*, by hand, by somebody who had thought to take one.
It did not make it not happen.

## Decision

**Memory is persisted; tasks and the audit log are not, yet, and for different reasons.**

Memory is the one the owner would miss. It is also the clean case: `MemoryManager` has exactly
one write path (`_store`) and one delete path (`forget`), both already async.

Tasks are deliberately excluded. Restoring a `RUNNING` task from a table produces a task that
*looks* alive with nothing driving it — the coroutine that was executing it died with the
process. Persisting tasks without resumption machinery would replace "the task is gone" with
"the task is stuck and appears to be working", which is worse. It needs its own sprint, with
the resumption story designed rather than assumed.

The audit log is excluded because `AuditLog.record` is synchronous and persistence is not.
Making it async is a change across every call site that records anything, and that deserves to
be its own change rather than a rider on this one. The gap is real and named.

**The table is the truth; the dict is an index.** `MemoryManager` keeps `_records` because
recall walks it every turn and a database round trip per candidate would be absurd. But it is
loaded *from* the table at startup and written *through* on every change. Two stores that can
disagree are worse than one store and no persistence, because the disagreement is invisible
and the wrong one wins at random.

**A failed write fails the operation.** `remember` returning a record it did not persist is a
lie the owner discovers after a restart, when there is nothing to be done. The exception
propagates while the thing they said is still on the screen.

**Forgetting reaches the table.** A memory dropped from the index and left in storage comes
back on the next restart — the failure mode the owner would least expect and least easily
notice (ADR 0019).

**No database is a supported configuration, and says so.** `NullRepository` is the offline
adapter (ADR 0001): the whole test suite runs on it, `python -m apps.cli` runs on it, and a
deployment that wants an ephemeral assistant gets one. It is not a degraded mode and does not
warn. What it must never be is a silent assumption, which is why `Container.persistent`
answers the question directly rather than leaving it to be inferred.

**Loading is async, so it is not part of construction.** `build_container` stays synchronous
and a separate `start(container)` loads state. A container that reached for a database while
being assembled could not be built in a test — and every existing test still passes without
calling `start`, which is the check that this is additive.

**Rows that all fail to load are an error, not an empty start.** A startup line reading
`memories=0` looks identical to a first boot. That is how somebody spends a week not noticing
their assistant has amnesia.

## Consequences

- The headline limitation is closed for the state that matters most.
- Two bugs surfaced while wiring it, both of which would have shipped:
  - the repository mapped only outward, so the row it had just written would not load back —
    the table carries `user_id` and `updated_at`, which `MemoryRecord` forbids;
  - SQLite has no timezone type, so a restored memory came back naive and **crashed the first
    `recall` after a restart** — a worse failure than not persisting at all, because it breaks
    a working system at exactly the moment persistence was supposed to help.
- **Cost we accepted:** every write is now a round trip. For a personal assistant writing a
  handful of memories a day this is irrelevant, and the alternative — batching — reintroduces
  the window where the index and the table disagree.
- Embeddings are stored and reloaded rather than recomputed. Re-embedding on every boot would
  be slow, and worse, a change of embedding model would silently re-score every memory.

## Alternatives considered

- **Snapshot to a file periodically.** Rejected: it makes durability depend on cadence, so the
  answer to "did it save my note" is "probably". Sprint 47's backup already covers the
  take-a-copy case properly.
- **Read through to the database on every recall.** Rejected: recall walks every candidate,
  and the latency would be paid on every turn to remove a cache that is correct by
  construction.
- **Persist tasks too, for completeness.** Rejected — see above. Completeness is not the goal;
  not lying about state is.
- **Warn on every write when running without a database.** Rejected: a warning on every write
  is a warning people filter out, and it would fire constantly in the test suite. One readable
  flag beats a thousand log lines.
