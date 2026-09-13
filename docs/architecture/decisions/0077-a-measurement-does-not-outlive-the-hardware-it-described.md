# 0077. A measurement does not outlive the hardware it described

Date: 2026-09-13

## Status

Accepted. Implements ADDENDUM §25. Extends
[0046](0046-a-privacy-rule-expressed-as-a-score-is-a-preference.md) and follows the
persistence shape of Sprint 51.

## Context

[§23](../../23-release-readiness.md) listed the gap and named what was blocking it:

> **Benchmarks do not survive a restart.** The `models` table has `tokens_per_second` and
> `last_benchmarked_at` waiting for them. Persisting needs a decision about whether a
> measurement taken before a hardware change should outlive it, and guessing that is worse
> than restarting the window.

Measured against the shipped code:

```
AFTER 5 REAL CALLS: 500.0 tok/s | profiles held: 1
AFTER A RESTART:      0.0 tok/s | profiles held: 1
FRESHNESS WINDOW: 14 days
REPOSITORY WIRED: None
```

`BenchmarkBook.__init__` took a `repository`, assigned it to `self._repository`, and never
read it again. One line, and it was the only line: a persistence hook that persisted nothing.

The consequence is worse than "measurements are lost". `MAX_AGE` is fourteen days, and
`fresh()` filters every sample against it on every read. **That window had never once
applied**, because nothing in it could live long enough to reach it. The code that expires
stale measurements was correct, tested, and unreachable — the same shape §23 already records
for the spend ledger, where "the rows come back on the next start and the window never
applies".

And zero is not a neutral loss. `tokens_per_second` returns `0.0` for *unmeasured*, which the
router reads as unknown rather than slow (ADR 0046). So every restart returned the whole house
to "nothing has ever been measured", and the routing improvement §25 exists to provide reset
itself on a timer nobody chose.

## Decision

**A measurement is kept while the hardware it describes still answers to the same
description, and discarded when it does not.**

That is §23's blocking decision, and it does not need a guess. A measurement is a fact about
a model *running on particular hardware*. The machine reports what hardware it has, so the
question "is this number still about anything?" is answered by reading the machine — the same
principle ADR 0012 applies to verification, where a verdict is observed rather than supplied.

The fingerprint is the parts that change what a model does: GPU name, VRAM, RAM, cores.
Deliberately **not** the hostname, and not the device id — renaming a machine does not make
last week's throughput wrong, and the device id is already half the key.

It is stamped **at record time**, not compared at restore time against whatever is present
then. The stored value is the fact; the current value is what it is compared against.

Three values that look alike and are kept apart:

* **A real profile** — hardware the machine described.
* **`"unreported"`** — a local machine that has not said yet. It restores onto another
  machine that also has not said, and never onto a known, different one.
* **`"cloud"`** — a provider, whose hardware Thursday cannot see at all. A cloud measurement
  is never discarded for hardware because there is nothing to compare; the freshness window
  is its only bound, and the constant exists to say that rather than to imply a check that is
  not happening.

Collapsing the second into the third would let a measurement taken before a report survive a
swap, which is the exact failure this ADR is about.

**Not the `models` table**, which §23 suggested and which looks like the obvious home.
`models.tokens_per_second` carries what the *node reported about itself*, and the registry
rewrites it on every reconnect. A second writer putting measurements there would produce the
two-stores-that-disagree failure `persistence.py` warns about, with the reconnect winning at
random. A separate table has one writer.

**Written through in batches, not on every call.** `record` is synchronous and sits on the
path of every inference. A flush every two minutes, plus one on clean shutdown, costs a
handful of samples out of a fifty-sample window if the process dies between them; an await
per inference costs every call. A book with no database still flushes into the null store, or
the dirty set grows for the life of the process.

**The row id is derived from the profile key** (`uuid5`), so a profile rewrites its own row
rather than adding one on every flush.

## Consequences

Three separate processes, a real SQLite database, the real container:

```
MEASURED: 500.0 tok/s
FLUSHED : 1 profile(s) to the database
AFTER A REAL RESTART: 500.0 tok/s
AFTER A GPU SWAP    : 0.0 tok/s   <- the measurement died with the hardware
```

Checked by reverting:

| Mutation | Red |
|---|---|
| A measurement outlives the hardware it described | 3 |
| The fourteen-day window is not applied on restore | 3 |
| The row id is not derived from the key | 3 |
| One unreadable sample fails the whole restore | 1 |

A discarded profile is **removed**, not left in place: left there it would be re-read and
re-rejected on every start, for ever. A profile with nothing inside the window is removed for
the same reason, so the table does not accumulate a permanent record of every model ever
tried.

**Persistence is off by default**, like every other persistence flag here. No database is a
supported configuration — the whole suite runs on `NullRepository` — and what a deployment
must not get is a quiet promise of durability it will not keep. `Container.persistent` says
which it is.

**What this does not do.** A model re-quantised in place, or a driver update, changes what the
number means and changes nothing the machine reports. The fourteen-day window is the only
thing that catches those, which is what it was always for — and now, for the first time, it
can actually reach.
