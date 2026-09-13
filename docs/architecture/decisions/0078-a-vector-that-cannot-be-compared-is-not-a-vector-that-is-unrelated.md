# 0078. A vector that cannot be compared is not a vector that is unrelated

Date: 2026-09-13

## Status

Accepted. Extends [0036](0036-the-table-is-the-truth-the-dict-is-an-index.md).

## Context

Three sprints in a row found the same shape: a capability that existed and had no caller —
`switch_model` as `lambda: None` (ADR 0072), `BenchmarkBook._repository` assigned and never
read (ADR 0077), `ComputeExecutor`'s quality gate with no production caller. So this one
started by auditing for it rather than guessing: every private attribute assigned in an
`__init__` and never read again.

Three hits in the whole tree. Two were benign — `MediaEditTool._workdir` duplicates a guard
that `FFmpegEditor.allowed_roots` already enforces on every operation (verified firing), and
`Briefer._memory` is an unused constructor argument. The third ran further than the attribute.

### Four declarations, three numbers

```
EMBEDDER          : HashEmbeddingProvider
SETTINGS SAY      : 256 dimensions      (settings.yaml, overriding the field default)
SCHEMA COLUMN SAYS: 768 dimensions      (EMBEDDING_DIMENSIONS)
ACTUAL VECTOR LEN : 256
MATCHES COLUMN?   : False
```

`PgVectorStore.__init__` took `dimensions`, stored it in `self._dimensions`, and never read
it — so the one object that knew exactly how wide its column was wrote whatever it was
handed. `EmbeddingProvider.dimensions` exists on the port and nothing compares it to
anything. `settings.yaml` shipped 256 against a `Vector(768)` column.

On SQLite that column is `Text()`, which accepts any width and refuses nothing — which is why
the whole suite passed. On Postgres the same column is `vector(768)`, which does not.

### Why nobody noticed

```
STORED: one 256-dim vector and one 768-dim vector
SEARCH WITH A 768-DIM QUERY RETURNED: 2 hit(s)
   768-dim memory scored 1.0000
   256-dim memory scored 0.0000
```

`cosine` returns `0.0` when the widths differ — **the same number it returns for two vectors
that genuinely are orthogonal.** A memory written before an embedder change therefore comes
back as a *considered and rejected* result, for ever, and nothing distinguishes that from a
memory that was read and found irrelevant.

`MemoryManager.restore`'s own docstring says re-embedding is avoided because changing the
embedding model "would silently re-score every memory the owner has". It was right about the
risk and nothing checked for it.

The second consequence is quieter. `_nearest` picks a conflict candidate with
`max(pool, key=cosine)`; against records of another width every score is `0.0`, so `max`
returns an arbitrary record and reports it as the nearest match at similarity zero. `
_is_conflict` calls a paraphrase a conflict at `>= 0.90`, so that test could never fire
again. The keyed test still could, which is exactly what made the loss invisible.

## Decision

**Separate "I cannot judge this" from "this is not similar."** `comparable(a, b)` is the
predicate; `cosine` keeps returning `0.0`, because most callers are right to carry on.

That split matters more than replacing `cosine`. A recall blends similarity with recency,
importance and a lexical overlap, so an unscorable memory is ranked worse rather than lost —
and `forget_about` already takes `max(cosine, lexical)` for stated reasons. The callers that
must *not* carry on are the ones where `0.0` is a decision:

* **A vector search**, whose entire output is a similarity. It now skips what it cannot
  compare and reports `unreadable` and `widths`, rather than returning a number that is not
  a similarity.
* **A conflict check**, where `max` over all-zero scores names an arbitrary record. It now
  considers only comparable records, so no candidate is better than a wrong one.

**The store that knew, checks.** `PgVectorStore` uses `_dimensions` to refuse a vector its
column cannot hold. The check lives there because Postgres answers this at insert time and
SQLite never answers at all, and a store that behaves differently by backend is a store whose
tests prove nothing about production.

**The declarations agree**, and `settings.yaml` was the one that was wrong: 768 matches the
column and matches `nomic-embed-text`, the local embedder §2 names. 256 matched nothing.

**Startup measures rather than reads.** `EmbeddingProvider.dimensions` is a *declaration* —
`OllamaEmbeddingProvider` announces 768 whatever model it is pointed at, so an owner who
switches to a 1024-wide model has a provider that is simply wrong about itself. `start()`
embeds one short string and counts what that width cannot reach, which is §12's principle
that verification reads the machine rather than taking a report.

**It never refuses to start.** An assistant that will not come up because its recall is
degraded is worse than one that comes up and says so: the memories are all still stored,
still recalled by text, and everything else still works. What it must not do is come up
silently.

## Consequences

Changing `embedding_dimensions` strands every memory embedded at the old width — which is
the harm this ADR is about, so it is not done quietly: the count, and the widths present,
are named at every start.

Checked by reverting:

| Mutation | Red |
|---|---|
| Search returns what it could not judge | 1 |
| The conflict check compares against records it cannot judge | 1 |
| The production store writes a vector its column cannot hold | 1 |
| `settings.yaml` goes back to disagreeing with the column | 5 |
| The check trusts the declared width instead of measuring | 1 |

**What is still not proved here.** pgvector is not installed in this container and there is
no Postgres to run against, so the claim that Postgres refuses a wrong-width insert is read
from the type shim and pgvector's documented behaviour, **not observed**. What is observed is
that the shim asks for `PGVector(768)` on the postgresql dialect and `Text()` otherwise, and
that the default configuration produced 256. Closing that last step needs a Postgres, the
same way the keychain and local-AI gaps need hardware.

`PgVectorStore` is still constructed nowhere: every deployment builds `InMemoryVectorStore`,
so vector search is a brute-force scan over the restored embeddings. That is a separate gap
and this ADR does not close it.
