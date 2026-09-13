"""The schema against a real PostgreSQL with pgvector (§2) — Sprint 105.

[ADR 0078](../../docs/architecture/decisions/0078-a-vector-that-cannot-be-compared-is-not-a-vector-that-is-unrelated.md)
closed a defect it could not finish proving, and said so:

> pgvector is not installed in this container and there is no Postgres to run against, so
> the claim that Postgres refuses a wrong-width insert is read from the type shim and
> pgvector's documented behaviour, **not observed**.

That turned out to be a wrong reading of the constraint rather than a real one. The blocked
thing here is downloading *model weights*; `postgresql-16` and `postgresql-16-pgvector` are
both in the distribution's own package index, and `pgvector` and `asyncpg` are both on PyPI.
Everything needed was one `apt-get install` away, and the gap was in what had been tried.

So this file finishes the job. Every test here needs a real server and is **skipped** — not
xfailed — when there is none, exactly as the live keychain suite is (ADR 0074), and for the
same reason: a skip on a developer's laptop is correct, and a skip in CI means the setup
stopped working, which is why CI asserts the server is really there before the suite runs.

What a real server proves that SQLite cannot: `Vector` is `Text()` on SQLite, which accepts
any width and refuses nothing. On Postgres it is `vector(768)`, which is where the shipped
256-dimension default stopped being a mismatch on paper and became an assistant that could
not write a single memory.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from thursday_core.config import Settings
from thursday_core.container import build_container, start
from thursday_shared.db.models import EMBEDDING_DIMENSIONS
from thursday_shared.enums import MemoryLayer
from thursday_shared.models import MemoryWrite

from tests.integration.postgres_live import OWNER_SQL, live

pytestmark = pytest.mark.skipif(
    not live.available,
    reason=f"no live PostgreSQL with pgvector reachable ({live.reason}) — set "
    "THURSDAY_TEST_DATABASE_URL to a server that has the extension",
)


@pytest.fixture
async def db():
    """A migrated database with an owner row, emptied of memories between tests."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(live.url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM memories"))
        await conn.execute(text(OWNER_SQL))
    yield engine
    await engine.dispose()


def settings_at(width: int) -> Settings:
    return Settings(
        database_url=live.url,
        persist_memory=True,
        embedding_dimensions=width,
    )


# ------------------------------------------------------------------ the schema is real


async def test_the_migrations_produced_a_vector_column_of_the_declared_width(db):
    """`EMBEDDING_DIMENSIONS` is a number in Python until a server agrees with it."""
    async with db.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT table_name, format_type(a.atttypid, a.atttypmod) "
                    "FROM information_schema.columns c "
                    "JOIN pg_attribute a ON a.attrelid = (c.table_name)::regclass "
                    "AND a.attname = c.column_name WHERE udt_name = 'vector' "
                    "ORDER BY table_name"
                )
            )
        ).all()

    assert rows, "the schema declares no vector columns at all"
    for table, declared in rows:
        assert declared == f"vector({EMBEDDING_DIMENSIONS})", f"{table} is {declared}"


# ------------------------------------------------------------------ what SQLite hid


async def test_a_memory_at_the_declared_width_is_written_and_read_back(db):
    container = build_container(settings_at(EMBEDDING_DIMENSIONS))
    await start(container)

    record = await container.memory.write(
        MemoryWrite(content="แมวของฉันชื่อมะลิ", layer=MemoryLayer.SEMANTIC), force=True
    )

    assert record is not None
    async with db.begin() as conn:
        stored = (
            await conn.execute(
                text("SELECT vector_dims(embedding), content FROM memories WHERE id = :i"),
                {"i": record.id},
            )
        ).one()
    assert stored[0] == EMBEDDING_DIMENSIONS
    assert stored[1] == "แมวของฉันชื่อมะลิ", "Thai content must survive the round trip"


async def test_the_configuration_this_project_ships_can_write_a_memory(db):
    """The one that would have caught it.

    Every other test here names a width, which means none of them exercises what an owner
    actually gets. This one takes the shipped configuration — `settings.yaml`, unmodified —
    and asks the only question that matters: can the thing we ship write a memory to the
    database §2 says it uses. Before ADR 0078 the answer was no, on every deployment, and
    the whole suite was green because SQLite's `Vector` is a `Text()` column.
    """
    container = build_container(Settings(database_url=live.url, persist_memory=True))
    await start(container)

    record = await container.memory.write(
        MemoryWrite(content="การตั้งค่าที่แจกจริงต้องเขียนได้", layer=MemoryLayer.SEMANTIC),
        force=True,
    )
    assert record is not None

    async with db.begin() as conn:
        width = (
            await conn.execute(
                text("SELECT vector_dims(embedding) FROM memories WHERE id = :i"),
                {"i": record.id},
            )
        ).scalar_one()
    assert width == EMBEDDING_DIMENSIONS


async def test_a_memory_at_the_old_default_width_is_refused_by_the_server(db):
    """The observation ADR 0078 could not make. `settings.yaml` shipped 256 against a
    `vector(768)` column: on SQLite that is a mismatch nothing notices, and here it is an
    assistant that cannot write a single memory."""
    container = build_container(settings_at(256))
    await start(container)

    with pytest.raises(Exception, match="expected 768 dimensions, not 256"):
        await container.memory.write(
            MemoryWrite(content="แมวของฉันชื่อมะลิ", layer=MemoryLayer.SEMANTIC), force=True
        )


async def test_nothing_is_left_behind_when_the_server_refuses(db):
    """A refused insert must not leave a half-written row — the record the manager holds in
    memory and the row the table holds would then disagree, which is the failure
    `persistence.py` exists to prevent."""
    container = build_container(settings_at(256))
    await start(container)

    with pytest.raises(Exception, match="expected 768 dimensions"):
        await container.memory.write(
            MemoryWrite(content="ไม่ควรถูกเขียน", layer=MemoryLayer.SEMANTIC), force=True
        )

    async with db.begin() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM memories"))).scalar_one()
    assert count == 0


async def test_the_extension_is_the_one_the_schema_needs(db):
    async with db.begin() as conn:
        version = (
            await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'"))
        ).scalar_one_or_none()
    assert version is not None, "pgvector is not installed in this database"
