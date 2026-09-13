# 0080. Code nobody calls cannot be found to be broken

Date: 2026-09-13

## Status

Accepted. Follows [0079](0079-what-was-blocked-was-the-weights-not-the-database.md), which made
a real server available to test against.

## Context

`PgVectorStore` describes itself as "the production path" in its own module docstring, and
[§4](../../01-architecture.md) names a swappable vector store as an architectural goal:
*"swap the vector store or the agent framework without rewriting the system"*.

The first call anything in this project's history ever made to it:

```
DataError: invalid input for query argument $1:
[0.5, 0.5, 0.5, ...] (expected str, got list)
```

asyncpg has no encoder for a type the server registered at runtime, so a bound Python list is
not a vector to it. `upsert` had the same bug. The store did not work at all.

That is a plain defect, and the interesting part is why it survived from the day it was
written. Three facts, each hiding the next:

1. **`search` has no caller anywhere in the system.** `MemoryManager` calls `upsert` on every
   write (lines 485, 744) and `delete` on every forget (line 378), and `recall` scores cosine
   in Python over its own `_records`. The port is written to on every memory and read from by
   nobody.
2. **Nothing constructs `PgVectorStore`.** `container.py` builds `InMemoryVectorStore()`
   unconditionally, persistent deployment or not.
3. **So it had never been executed**, and an encoding bug on its first bound parameter sat
   there untouched.

Each layer explains the one below it. A port with no reader is not exercised; a class nobody
builds is not run; code that is not run is not discovered to be broken. This is the same shape
as ADR 0072's repair handlers and ADR 0077's persistence hook, found by the same audit habit —
except this one needed a real PostgreSQL to see at all, which is what ADR 0079 made possible.

## Decision

**Repair the store, and prove it against a real server.** A vector is bound in pgvector's
documented text form, `[0.1,0.2,…]`, with an explicit `::vector` cast at every use site. The
alternative — registering pgvector's asyncpg codec on every pooled connection through a
SQLAlchemy event hook — works too, and was rejected for needing a hook, a driver-specific
import in this module, and a way to be silently absent on a connection nobody hooked.

**A forgotten memory stops being a result.** `delete` sets `embedding = NULL` rather than
removing the row, and `<=>` against NULL is NULL — which sorts last on PostgreSQL but still
occupies a row of the `LIMIT`. A corpus with more forgotten memories than remembered ones
would return fewer hits than it asked for, with nothing saying why. `embedding IS NOT NULL`
is now part of the predicate.

**What is deliberately not decided here: whether `recall` should route through the store.**
That is a ranking question, not a repair. `recall` currently scores *every* candidate and
blends similarity with recency, importance and a lexical overlap; a store returns top-k by
similarity alone, so routing through it truncates the candidate set before the blend and can
rank differently. For the personal corpus this product is built for, the in-Python scan is
what the module docstring already calls "fast enough". Wiring it is a decision about search
quality that deserves its own measurement, and guessing at it inside a bug fix is how a
repair becomes a regression.

What has changed is that the decision is now *available*. Before this, "wire up the vector
store" would have meant debugging an unexecuted class at the same time as changing ranking.

## Consequences

Against a real PostgreSQL 16 with pgvector 0.6.0, through memories written by the real
application: `search` returns the cat memory first for "แมวกินอะไร" and scores it above the
one about petrol; a vector written by `upsert` comes back from `search` at 1.0; a memory
passed to `delete` disappears from results while its neighbours stay.

Checked by reverting:

| Mutation | Red |
|---|---|
| A bound Python list again — the shape that never worked | 6 |
| Similarity returned as a distance (sign the wrong way round) | 2 |
| Forgotten memories still occupy a row of the LIMIT | 1 |
| The store writes a width its column cannot hold | 1 |

**Still true and now written down:** the vector-store port is write-only in practice. §4's
swappability is real — there are two working implementations and the manager never learns
which it has — but nothing reads either of them. That is a gap in the system, not in this
store, and naming it is the point of this ADR.
