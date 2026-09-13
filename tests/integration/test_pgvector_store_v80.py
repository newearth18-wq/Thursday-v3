"""The store the docstring calls "the production path" (§4) — Sprint 106.

`PgVectorStore` had never been constructed. The first call anything ever made to it failed
on its first bound parameter:

    DataError: invalid input for query argument $1:
    [0.5, 0.5, ...] (expected str, got list)

asyncpg has no encoder for a type the server registered at runtime, so a bound Python list
is not a vector to it. That is a plain bug, and it survived because of a third fact found
above it:

**`search` has no caller anywhere in the system.** `MemoryManager` calls `upsert` on every
write and `delete` on every forget, and scores cosine in Python over its own records when
recalling. So the port is written to on every memory and read from by nobody — and code that
nobody calls cannot be discovered to be broken.

Three layers, and each one hid the next: no caller, so never constructed; never constructed,
so never executed; never executed, so the encoding bug sat there from the day it was written.

This file gives the store a real exercise against a real server. Whether `recall` should
route through it is a ranking decision, not a repair, and is deliberately not made here —
see [§23](../../docs/23-release-readiness.md).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from thursday_core.config import Settings
from thursday_core.container import build_container, start
from thursday_memory.vector import PgVectorStore, as_vector
from thursday_shared.db.models import EMBEDDING_DIMENSIONS
from thursday_shared.enums import MemoryLayer
from thursday_shared.models import MemoryWrite

from tests.integration.postgres_live import OWNER_SQL, live

pytestmark = pytest.mark.skipif(
    not live.available,
    reason=f"no live PostgreSQL with pgvector reachable ({live.reason})",
)


@pytest.fixture
async def store():
    engine = create_async_engine(live.url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM memories"))
        await conn.execute(text(OWNER_SQL))
    yield PgVectorStore(
        async_sessionmaker(engine, expire_on_commit=False), dimensions=EMBEDDING_DIMENSIONS
    )
    await engine.dispose()


@pytest.fixture
async def remembered():
    """Two memories written by the real application, embeddings and all."""
    container = build_container(Settings(database_url=live.url, persist_memory=True))
    await start(container)

    async def write(content: str):
        return await container.memory.write(
            MemoryWrite(content=content, layer=MemoryLayer.SEMANTIC), force=True
        )

    cat = await write("แมวชอบกินปลาทู")
    car = await write("รถยนต์ใช้น้ำมันเบนซิน")
    return container, cat, car


# ----------------------------------------------------------------- the encoding


def test_a_vector_is_encoded_in_the_form_pgvector_reads():
    """The repair. A bound Python list is not a vector to asyncpg; this text form is what
    pgvector documents for input, and needs no codec registered on every pooled connection."""
    assert as_vector([0.5, -1.0, 0.0]) == "[0.5,-1.0,0.0]"
    assert as_vector([]) == "[]"


async def test_the_store_can_be_searched_at_all(store, remembered):
    """The call that used to raise on its first parameter."""
    container, cat, _ = remembered
    query = (await container.embedder.embed(["แมวกินอะไร"]))[0]

    hits = await store.search(query, k=5)

    assert hits, "the store returned nothing for a corpus that has two memories in it"
    assert hits[0][0] == cat.id


async def test_similarity_comes_back_the_right_way_round(store, remembered):
    """`1 - (embedding <=> v)`: higher is more similar. A sign error here would rank the
    corpus backwards while still returning plausible-looking numbers."""
    container, cat, car = remembered
    query = (await container.embedder.embed(["แมวกินอะไร"]))[0]

    scores = dict(await store.search(query, k=5))

    assert scores[cat.id] > scores[car.id]


async def test_a_vector_written_by_the_store_is_found_by_the_store(store, remembered):
    """`upsert` had the same encoding bug as `search` and the same reason nobody knew."""
    _, cat, _ = remembered
    exact = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)

    await store.upsert([(cat.id, exact, {})])
    hits = await store.search(exact, k=1)

    assert hits[0][0] == cat.id
    assert hits[0][1] == pytest.approx(1.0, abs=1e-6)


async def test_a_forgotten_memory_stops_being_a_result(store, remembered):
    """`delete` sets the embedding to NULL rather than removing the row, and `<=>` against
    NULL is NULL — which sorts last on Postgres and still occupies a row of the LIMIT. A
    corpus with more forgotten memories than remembered ones would return fewer hits than it
    should, with nothing saying why."""
    _, cat, car = remembered
    exact = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)
    await store.upsert([(cat.id, exact, {})])

    await store.delete([cat.id])
    found = [item for item, _ in await store.search(exact, k=5)]

    assert cat.id not in found
    assert car.id in found, "forgetting one memory must not hide the others"


async def test_the_store_refuses_a_width_its_column_cannot_hold(store):
    """ADR 0078's check, now exercised by the store that actually has a column."""
    from thursday_shared.ids import new_id

    with pytest.raises(ValueError, match="could never be compared"):
        await store.upsert([(new_id(), [0.5] * 256, {})])


async def test_an_empty_corpus_answers_with_nothing_rather_than_raising(store):
    assert await store.search([0.5] * EMBEDDING_DIMENSIONS, k=5) == []
