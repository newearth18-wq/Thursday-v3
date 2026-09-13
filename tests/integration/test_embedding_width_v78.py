"""A vector that cannot be compared is not a vector that is unrelated — Sprint 104.

Found by auditing for private attributes assigned in `__init__` and never read again — the
shape three earlier sprints each turned out to be hiding something. `PgVectorStore` was one
of three hits, and the thread from it ran further than the attribute.

**Four places declared the embedding width. Three of them disagreed.**

    EMBEDDER          : HashEmbeddingProvider
    SETTINGS SAY      : 256 dimensions      (settings.yaml, overriding the field default)
    SCHEMA COLUMN SAYS: 768 dimensions      (EMBEDDING_DIMENSIONS)
    ACTUAL VECTOR LEN : 256
    MATCHES COLUMN?   : False

Nothing compared any of them. `PgVectorStore.__init__` took `dimensions`, stored it, and
never read it — so the one object that knew how wide its column was wrote whatever it was
handed.

And the reason that stayed invisible is the sharp part:

    STORED: one 256-dim vector and one 768-dim vector
    SEARCH WITH A 768-DIM QUERY RETURNED: 2 hit(s)
       768-dim memory scored 1.0000
       256-dim memory scored 0.0000

`cosine` returns 0.0 for vectors of different widths — the same number it returns for two
vectors that really are orthogonal. So a memory written before an embedder change is
returned as a *considered and rejected* result, for ever, and nothing anywhere says so.
`MemoryManager.restore`'s own docstring warned that changing the embedding model "would
silently re-score every memory the owner has". It was right, and nothing checked.
"""

from __future__ import annotations

import pytest
from thursday_core.config import Settings
from thursday_core.container import _check_embedding_width, build_container
from thursday_memory.embeddings import comparable, cosine
from thursday_memory.vector import InMemoryVectorStore, PgVectorStore
from thursday_shared.db.models import EMBEDDING_DIMENSIONS
from thursday_shared.enums import MemoryLayer
from thursday_shared.ids import new_id
from thursday_shared.models import MemoryRecord

NARROW, WIDE = 256, 768


def remembered(width: int, content: str = "แมวของฉันชื่อมะลิ") -> MemoryRecord:
    return MemoryRecord(content=content, layer=MemoryLayer.SEMANTIC, embedding=[0.5] * width)


# ----------------------------------------------------------------- the four declarations


def test_every_declaration_of_the_embedding_width_agrees():
    """The defect this sprint came from: a shipped `settings.yaml` saying 256, a column
    saying 768, and an embedder that believed the settings file."""
    assert Settings().embedding_dimensions == EMBEDDING_DIMENSIONS


async def test_what_the_embedder_actually_produces_matches_the_column():
    """Measured, not read off `dimensions` — that attribute is a declaration, and
    `OllamaEmbeddingProvider` announces 768 whatever model it is pointed at."""
    container = build_container()
    produced = await container.embedder.embed(["ตรวจความกว้าง"])
    assert len(produced[0]) == EMBEDDING_DIMENSIONS


# ----------------------------------------------------------------- unjudgeable vs unrelated


def test_a_width_mismatch_is_not_the_same_question_as_a_similarity():
    """0.0 was both answers. `comparable` is the one that separates them."""
    assert comparable([0.5] * WIDE, [0.5] * WIDE) is True
    assert comparable([0.5] * NARROW, [0.5] * WIDE) is False
    assert cosine([0.5] * NARROW, [0.5] * WIDE) == 0.0, "the old answer, kept for blending"


async def test_a_search_does_not_return_what_it_could_not_judge():
    """The measured failure: two stored vectors, one of them unjudgeable, both returned —
    the unjudgeable one scoring exactly what an irrelevant one would."""
    store = InMemoryVectorStore()
    narrow, wide = new_id(), new_id()
    await store.upsert([(narrow, [0.5] * NARROW, {}), (wide, [0.5] * WIDE, {})])

    hits = await store.search([0.5] * WIDE, k=5)

    assert [item for item, _ in hits] == [wide]
    assert store.unreadable(WIDE) == 1
    assert store.widths() == {NARROW: 1, WIDE: 1}


async def test_a_search_still_returns_everything_it_can_judge():
    store = InMemoryVectorStore()
    ids = [new_id() for _ in range(3)]
    await store.upsert([(i, [0.5] * WIDE, {}) for i in ids])

    assert len(await store.search([0.5] * WIDE, k=5)) == 3
    assert store.unreadable(WIDE) == 0


async def test_one_width_stored_is_the_healthy_case():
    store = InMemoryVectorStore()
    await store.upsert([(new_id(), [0.1] * WIDE, {}) for _ in range(4)])
    assert store.widths() == {WIDE: 4}


# ----------------------------------------------------------------- the store that knew


async def test_the_production_store_refuses_a_vector_its_column_cannot_hold():
    """`_dimensions` was assigned and never read, so the one object that knew how wide its
    column was wrote whatever it was given. Postgres answers this at insert time and SQLite
    never answers at all, so the check belongs where both behave the same."""
    store = PgVectorStore(session_factory=None, dimensions=WIDE)

    with pytest.raises(ValueError, match="could never be compared"):
        await store.upsert([(new_id(), [0.5] * NARROW, {})])


# ----------------------------------------------------------------- conflicts stop quietly


async def test_a_conflict_check_ignores_records_it_cannot_compare():
    """`_is_conflict` calls a paraphrase a conflict at similarity >= 0.90. Against records of
    another width every similarity is 0.0, so that test could never fire again — and `max`
    over all-zero scores returned an arbitrary record as the "nearest match"."""
    container = build_container()
    stale = remembered(NARROW)
    container.memory._records[stale.id] = stale

    assert container.memory._nearest([0.5] * WIDE, layer=MemoryLayer.SEMANTIC, key=None) is None


async def test_a_conflict_check_still_sees_records_it_can_compare():
    container = build_container()
    current = remembered(WIDE)
    container.memory._records[current.id] = current

    found = container.memory._nearest([0.5] * WIDE, layer=MemoryLayer.SEMANTIC, key=None)
    assert found is not None
    record, similarity = found
    assert record.id == current.id
    assert similarity > 0.99


# ----------------------------------------------------------------- saying so at startup


async def test_startup_counts_the_memories_that_can_no_longer_be_reached():
    container = build_container()
    for _ in range(3):
        stale = remembered(NARROW)
        container.memory._records[stale.id] = stale

    assert await _check_embedding_width(container) == 3
    assert container.memory.embedding_widths() == {NARROW: 3}


async def test_startup_reports_nothing_when_every_memory_matches():
    container = build_container()
    current = remembered(EMBEDDING_DIMENSIONS)
    container.memory._records[current.id] = current

    assert await _check_embedding_width(container) == 0


async def test_startup_does_not_refuse_to_start_over_a_degraded_recall():
    """An assistant that will not start because recall is degraded is worse than one that
    starts and says so: the memories are all still there, still recalled by text, and
    everything else Thursday does still works."""
    container = build_container()
    stale = remembered(NARROW)
    container.memory._records[stale.id] = stale

    assert await _check_embedding_width(container) == 1  # reported, not raised


async def test_an_embedder_that_cannot_be_reached_does_not_stop_the_start():
    class Broken:
        dimensions = WIDE

        async def embed(self, texts):
            raise ConnectionError("Ollama is not running")

    container = build_container()
    container.embedder = Broken()
    assert await _check_embedding_width(container) == 0


async def test_a_provider_that_misdeclares_its_own_width_is_measured_anyway():
    """`OllamaEmbeddingProvider` announces 768 whatever model it is pointed at, so a switch
    to a 1024-wide model leaves a provider that is simply wrong about itself. The check
    embeds a probe rather than believing the attribute."""

    class Misdeclares:
        #: What it says about itself. `HashEmbeddingProvider` cannot stand in here: its
        #: `dimensions` is the same attribute `embed` reads, so it cannot be wrong.
        dimensions = WIDE

        async def embed(self, texts):
            return [[0.5] * NARROW for _ in texts]

    container = build_container()
    container.embedder = Misdeclares()
    stale = remembered(WIDE)
    container.memory._records[stale.id] = stale

    # Measured at 256, so the 768-wide memory is the one that cannot be reached — the
    # opposite of what the declaration would have concluded.
    assert await _check_embedding_width(container) == 1
