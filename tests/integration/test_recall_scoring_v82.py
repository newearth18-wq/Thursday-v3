"""Similarity for every candidate, not the nearest few (§7) — Sprint 108.

[ADR 0080](../../docs/architecture/decisions/0080-code-nobody-calls-cannot-be-found-to-be-broken.md)
repaired `PgVectorStore` and deliberately left one question open:

> Whether `recall` should route through the store is a ranking question, not a repair …
> a store returns top-k by similarity alone and truncates before the blend.

Measured, the answer is that it must not use `search` — and the number that settles it is in
`_score`: **similarity is weighted 0.30**, against 0.70 of recency, importance, project
relevance, source confidence and usage. So the k nearest are not the k best:

    PURE SIMILARITY ORDER (what a store's top-k returns):
      1. sim=0.2835  บันทึกทั่วไปเรื่องอาหาร ครั้งที่ 0
      ...
    ALLERGY MEMORY RANKS 13 OF 13 BY SIMILARITY ALONE

    WHAT recall() ACTUALLY RETURNS (blend: similarity is 30%):
      1. score=0.7550  เจ้าของแพ้อาหารทะเล ห้ามให้กุ้งปูเ

A pinned, maximum-importance memory the owner stated themselves — and a top-k of 3 cuts it
before the blend ever sees it. The owner asks about food and is not told they are allergic
to shellfish.

What the store *can* do is the arithmetic, with nothing truncated. Measured on this schema:

    10,000 memories -> Python loop 1,096 ms | pgvector, no LIMIT, 55 ms
    50,000 memories -> Python loop 5,569 ms | pgvector, no LIMIT, 460 ms

So the port grew `scores`, which returns every vector's similarity and ranks nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container
from thursday_memory.embeddings import cosine
from thursday_memory.vector import InMemoryVectorStore, PgVectorStore
from thursday_shared.enums import MemoryLayer
from thursday_shared.ids import new_id
from thursday_shared.models import MemoryQuery, MemoryRecord

WIDE = 768


# ----------------------------------------------------------------- the new capability


async def test_scores_returns_every_vector_and_ranks_nothing():
    """`search` answers "which are nearest"; `scores` answers "how near is each". The blend
    needs the second, and a k in the signature would be the bug this sprint is about."""
    store = InMemoryVectorStore()
    ids = [new_id() for _ in range(20)]
    await store.upsert([(i, [0.5] * WIDE, {}) for i in ids])

    found = await store.scores([0.5] * WIDE)

    assert set(found) == set(ids), "every stored vector, not the nearest handful"
    assert len(await store.search([0.5] * WIDE, k=5)) == 5, "search still truncates"


async def test_scores_leaves_out_what_it_cannot_compare():
    """ADR 0078: absent rather than 0.0, because a caller cannot tell "unrelated" from
    "unjudgeable" when both come back as zero."""
    store = InMemoryVectorStore()
    wide, narrow = new_id(), new_id()
    await store.upsert([(wide, [0.5] * WIDE, {}), (narrow, [0.5] * 256, {})])

    found = await store.scores([0.5] * WIDE)

    assert wide in found
    assert narrow not in found


async def test_scores_honours_a_filter():
    store = InMemoryVectorStore()
    keep, drop = new_id(), new_id()
    await store.upsert([(keep, [0.5] * WIDE, {"layer": "semantic"}), (drop, [0.5] * WIDE, {})])

    assert set(await store.scores([0.5] * WIDE, where={"layer": "semantic"})) == {keep}


# ----------------------------------------------------------------- what top-k would lose


async def test_the_best_answer_is_not_always_the_nearest_string():
    """The measurement this sprint turns on. A pinned, maximum-importance memory the owner
    stated ranks last by similarity and first by score — so a store's top-k would decide the
    blend's outcome by cutting its winner."""
    container = build_container()
    now = datetime.now(UTC)

    async def remember(content: str, *, importance: float, pinned: bool, age: int = 0):
        vector = (await container.embedder.embed([content]))[0]
        record = MemoryRecord(
            content=content,
            layer=MemoryLayer.SEMANTIC,
            embedding=vector,
            importance=importance,
            pinned=pinned,
            source="user",
            confidence=0.9,
            created_at=now - timedelta(days=age),
        )
        container.memory._records[record.id] = record
        # Into the store as well, so recall takes the store path rather than falling
        # back — otherwise a top-k wiring would never be exercised by this test at all.
        await container.vectors.upsert([(record.id, vector, {})])
        return record

    allergy = await remember("เจ้าของแพ้อาหารทะเล ห้ามให้กุ้งปู", importance=1.0, pinned=True)
    for i in range(12):
        await remember(f"บันทึกทั่วไปเรื่องอาหาร ครั้งที่ {i}", importance=0.1, pinned=False, age=200)

    query = "อาหาร"
    vector = (await container.embedder.embed([query]))[0]
    by_similarity = sorted(
        container.memory._records.values(),
        key=lambda r: cosine(vector, r.embedding or []),
        reverse=True,
    )
    rank = [r.id for r in by_similarity].index(allergy.id) + 1

    assert rank > 3, f"the allergy memory ranks {rank} by similarity — a top-k=3 drops it"

    recalled = await container.memory.recall(MemoryQuery(text=query, k=3))
    assert recalled[0].id == allergy.id, "the blend puts it first; recall must still find it"


# ----------------------------------------------------------------- asking the store


async def test_recall_asks_the_store_rather_than_looping_here():
    container = build_container()
    asked: list[int] = []

    class Counting(InMemoryVectorStore):
        async def scores(self, vector, *, where=None):
            asked.append(len(vector))
            return await super().scores(vector, where=where)

    container.memory._vectors = store = Counting()
    vector = (await container.embedder.embed(["อาหาร"]))[0]
    record = MemoryRecord(
        content="ชอบกาแฟดำ", layer=MemoryLayer.SEMANTIC, embedding=vector, source="user"
    )
    container.memory._records[record.id] = record
    await store.upsert([(record.id, vector, {})])

    await container.memory.recall(MemoryQuery(text="อาหาร", k=3))

    assert asked, "recall computed similarity itself instead of asking the store"


async def test_a_store_that_cannot_answer_degrades_rather_than_fails():
    """The embeddings are already in `_records`. A database that has gone away is a reason
    for recall to be slower, never a reason for it to be unavailable."""
    container = build_container()

    class Broken(InMemoryVectorStore):
        async def scores(self, vector, *, where=None):
            raise ConnectionError("the database went away")

    container.memory._vectors = Broken()
    vector = (await container.embedder.embed(["กาแฟ"]))[0]
    record = MemoryRecord(
        content="ชอบกาแฟดำไม่ใส่น้ำตาล",
        layer=MemoryLayer.SEMANTIC,
        embedding=vector,
        source="user",
    )
    container.memory._records[record.id] = record

    recalled = await container.memory.recall(MemoryQuery(text="กาแฟ", k=3))

    assert [r.id for r in recalled] == [record.id]
    assert recalled[0].score and recalled[0].score > 0


async def test_a_candidate_the_store_forgot_is_scored_here_not_zeroed():
    """Scoring it 0.0 because a store lost it would be ADR 0078's conflation again: the
    memory reads as "considered and irrelevant" when it was never considered."""
    container = build_container()

    class Forgetful(InMemoryVectorStore):
        async def scores(self, vector, *, where=None):
            return {}

    container.memory._vectors = Forgetful()
    # The record's embedding *is* the query vector, so its similarity is 1.0 and the 0.30
    # weight puts 0.30 of score on the line. An earlier version of this test used a merely
    # related sentence, where 0.30 x a small similarity fell inside the tolerance and the
    # assertion passed whether the fallback ran or not.
    vector = (await container.embedder.embed(["กาแฟ"]))[0]
    record = MemoryRecord(
        content="ชอบกาแฟดำไม่ใส่น้ำตาล",
        layer=MemoryLayer.SEMANTIC,
        embedding=list(vector),
        source="user",
    )
    container.memory._records[record.id] = record

    recalled = await container.memory.recall(MemoryQuery(text="กาแฟ", k=3))

    assert cosine(vector, record.embedding or []) == pytest.approx(1.0, abs=1e-9)
    zeroed = container.memory._score(record, 0.0, datetime.now(UTC), MemoryQuery(text="กาแฟ", k=3))
    assert recalled[0].score is not None
    assert recalled[0].score - zeroed == pytest.approx(0.30, abs=0.01), (
        "the forgotten candidate was scored 0.0 instead of falling back to a real similarity"
    )


# ----------------------------------------------------------------- which store is built


def test_a_sqlite_deployment_keeps_the_in_process_store():
    assert isinstance(build_container().vectors, InMemoryVectorStore)


def test_a_postgres_deployment_moves_the_arithmetic_to_the_server():
    container = build_container(
        Settings(database_url="postgresql+asyncpg://t@127.0.0.1:5432/t", persist_memory=True)
    )
    assert isinstance(container.vectors, PgVectorStore)


def test_postgres_without_persistence_keeps_the_in_process_store():
    """Nothing is in the table to score, so there is nothing for the server to do."""
    container = build_container(
        Settings(database_url="postgresql+asyncpg://t@127.0.0.1:5432/t", persist_memory=False)
    )
    assert isinstance(container.vectors, InMemoryVectorStore)


# ----------------------------------------------------------------- against a real server

from tests.integration.postgres_live import OWNER_SQL, live  # noqa: E402

needs_postgres = pytest.mark.skipif(
    not live.available, reason=f"no live PostgreSQL with pgvector ({live.reason})"
)


@pytest.fixture
async def postgres_backed():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(live.url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM memories"))
        await conn.execute(text(OWNER_SQL))
    await engine.dispose()

    from thursday_core.container import start

    container = build_container(Settings(database_url=live.url, persist_memory=True))
    await start(container)
    return container


@needs_postgres
async def test_the_server_and_the_python_loop_agree_on_every_similarity(postgres_backed):
    """The claim the whole change rests on: what moves to the server is the arithmetic, not
    the decision. If these disagree, recall has quietly started ranking differently."""
    from thursday_shared.models import MemoryWrite

    container = postgres_backed
    assert isinstance(container.vectors, PgVectorStore), "the fixture did not wire pgvector"

    for content in (
        "เจ้าของแพ้อาหารทะเล ห้ามให้กุ้งปู",
        "ชอบกาแฟดำไม่ใส่น้ำตาล",
        "ประชุมทีมทุกวันจันทร์เช้า",
        "ร้านอาหารใกล้บ้านปิดวันอังคาร",
    ):
        await container.memory.write(
            MemoryWrite(content=content, layer=MemoryLayer.SEMANTIC), force=True
        )

    vector = (await container.embedder.embed(["อาหาร"]))[0]
    candidates = list(container.memory._records.values())

    here = {r.id: cosine(vector, r.embedding or []) for r in candidates}
    there = await container.vectors.scores(vector)

    assert set(there) == set(here), "the server scored a different set of memories"
    for memory_id, score in here.items():
        # float4 on the server against float64 here; the tolerance is the storage width,
        # not a judgement about how close is close enough.
        assert there[memory_id] == pytest.approx(score, abs=1e-6)

    assert sorted(here, key=here.get, reverse=True) == sorted(  # type: ignore[arg-type]
        there,
        key=there.get,
        reverse=True,  # type: ignore[arg-type]
    ), "same numbers, different order — the ranking moved"


@needs_postgres
async def test_the_server_scores_everything_it_was_given(postgres_backed):
    """No LIMIT is the design, so a corpus larger than any plausible k comes back whole."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    container = postgres_backed
    vector = (await container.embedder.embed(["seed"]))[0]

    engine = create_async_engine(live.url, future=True)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO memories (user_id,layer,content,structured,importance,"
                "confidence,source,sensitivity,access_count,pinned,id,created_at,"
                "updated_at,embedding) "
                "SELECT '00000000-0000-0000-0000-000000000001','semantic','m'||g,"
                "'{}'::jsonb,0.5,0.8,'user',1,0,false,gen_random_uuid(),now(),now(),"
                "(:v)::vector FROM generate_series(1,500) g"
            ),
            {"v": __import__("thursday_memory.vector", fromlist=["as_vector"]).as_vector(vector)},
        )
    await engine.dispose()

    assert len(await container.vectors.scores(vector)) == 500
