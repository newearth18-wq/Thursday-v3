# 0082. The nearest few are not the best few

Date: 2026-09-13

## Status

Accepted. Settles the question [0080](0080-code-nobody-calls-cannot-be-found-to-be-broken.md)
left open, and depends on [0079](0079-what-was-blocked-was-the-weights-not-the-database.md)
for a server to measure against.

## Context

ADR 0080 repaired `PgVectorStore` and deliberately declined to wire `recall` through it:

> Whether `recall` should route through the store is a ranking question, not a repair … a
> store returns top-k by similarity alone and truncates before the blend. Guessing at it
> inside a bug fix is how a repair becomes a regression.

The number that settles it was already in `_score`, and is easy to walk past:

```python
score = (
    0.30 * similarity  # <- this is all the store's ranking knows about
    + 0.15 * recency
    + 0.18 * record.importance
    + 0.15 * relevance
    + 0.14 * source_confidence
    + 0.08 * usage
)
```

**Similarity is 0.30 of the answer.** A ranking built on it alone is a ranking built on
under a third of what decides. Measured against the shipped scorer, with one pinned,
maximum-importance memory the owner stated themselves and twelve old low-importance notes
that happen to share more surface with the query:

```
PURE SIMILARITY ORDER (what a store's top-k returns):
  1. sim=0.2835  บันทึกทั่วไปเรื่องอาหาร ครั้งที่ 0
  2. sim=0.2835  บันทึกทั่วไปเรื่องอาหาร ครั้งที่ 1
  3. sim=0.2835  บันทึกทั่วไปเรื่องอาหาร ครั้งที่ 2
ALLERGY MEMORY RANKS 13 OF 13 BY SIMILARITY ALONE

WHAT recall() ACTUALLY RETURNS (blend: similarity is 30%):
  1. score=0.7550  เจ้าของแพ้อาหารทะเล ห้ามให้กุ้งปูเด็ดขาด

WOULD A top-k=3 STORE HAVE PASSED IT TO THE BLEND? NO
```

The owner asks about food and is not told they are allergic to shellfish, because the thing
that would have told them was cut one layer below the thing that decides. That is not a
ranking nicety.

So `search` is the wrong method, and the open question has an answer: **no, `recall` must not
route through a vector store's top-k.**

### But the loop it replaces does not scale

```
   100 memories -> recall took    11.2 ms
 1,000 memories -> recall took   112.7 ms
10,000 memories -> recall took  1095.9 ms
50,000 memories -> recall took  5569.1 ms
```

Linear, with a 768-wide dot product per record in Python. The module docstring's "fast enough
for a personal corpus" is true at a thousand and visibly false at ten.

## Decision

**The problem was never the candidate set. It was the arithmetic.**

Both things above are true at once — top-k truncation loses answers, and the Python loop
does not scale — because they are answers to different questions. The store was being asked
*"which are nearest"* when the blend needs *"how near is each"*. The second question has no
`k` in it, so answering it truncates nothing:

```sql
SELECT id, 1 - (embedding <=> $1) FROM memories WHERE embedding IS NOT NULL
```

No `LIMIT`. Every candidate comes back scored; `MemoryManager` ranks them exactly as before.
What moves to the server is the dot product, not the decision.

```
10,000 memories -> Python loop 1,096 ms | pgvector, no LIMIT,  55 ms
50,000 memories -> Python loop 5,569 ms | pgvector, no LIMIT, 460 ms
```

Twenty times faster at ten thousand, with nothing dropped.

**So the port grew `scores`, and kept `search`.** They are different questions and both are
legitimate; what would have been wrong is using one where the other belongs. `scores` has no
`k` in its signature, which is the design stated in the type.

**`PgVectorStore` is built when the deployment is actually Postgres-backed** — which finally
gives the class ADR 0080 repaired a caller, and gives §4's swappable-store goal something to
swap. A SQLite deployment keeps the in-process store, where `scores` is the same arithmetic
in the same process and exists for the shape of the port rather than for speed.

**A store that cannot answer degrades rather than fails.** The embeddings are in `_records`
either way, so `_similarities` falls back to computing here and logs it. A database that has
gone away is a reason for recall to be slower, never a reason for it to be unavailable.

**A candidate the store does not return is scored here, not zeroed.** Scoring it `0.0`
because a store forgot it would be ADR 0078's conflation all over again: the memory reads as
*considered and irrelevant* when it was never considered.

## Consequences

Equivalence is the claim everything rests on, and it is asserted against a real server rather
than argued: every candidate scored by both paths, agreeing to `1e-6` (the difference is
`float4` on the server against `float64` here), in the same order.

Checked by reverting:

| Mutation | Red |
|---|---|
| `recall` uses the store's top-k instead of `scores` | 1 |
| `scores` returns `0.0` for widths it cannot compare | 1 |
| A candidate the store forgot is scored `0.0` | 1 |
| The container never builds `PgVectorStore` | 3 |
| A store that cannot answer fails the recall | 1 |

Two of those tests had to be strengthened before they bit. The forgotten-candidate test
compared scores with `abs=0.01` on a pair whose similarity was small enough that `0.30 ×`
it fell inside the tolerance — so it passed whether the fallback ran or not; it now uses a
record whose embedding *is* the query vector, putting the full `0.30` on the line. And the
allergy test only ever populated `_records`, never the store, so the store path it exists to
protect was not being exercised at all.

**What this does not do.** The in-memory store gains nothing but a method: the same loop, in
the same process. The speed is only available to a Postgres deployment, and §23's desktop
edition — no database server, by design — keeps the linear scan and its ceiling. That is a
stated limit of that deployment rather than something left undone here.

Nor does it make the *blend* cheap. Scoring is now one query plus a pass over the candidates
in Python; at some corpus size that pass becomes the cost. Nothing measured says where, and
guessing at it is how the last deferred question got deferred.
