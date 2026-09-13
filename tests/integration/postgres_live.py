"""Is there a real PostgreSQL with pgvector to test against? (Sprint 105)

Separate from the test module so the detection runs once at import and the skip reason can
say *why* — "no server" and "a server without the extension" are different problems and a
single boolean would hide the second one behind the first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: The owner row every memory's foreign key points at. Seeded rather than assumed: a bare
#: migrated database has no users, and a foreign-key error would read as a schema fault.
OWNER_SQL = (
    "INSERT INTO users (id, email, display_name, locale, timezone, proactivity_level, "
    "voice_profile, settings, created_at, updated_at) VALUES "
    "('00000000-0000-0000-0000-000000000001', 'owner@example.test', 'Owner', 'th-TH', "
    "'Asia/Bangkok', 1, '{}', '{}', now(), now()) ON CONFLICT (id) DO NOTHING"
)


@dataclass(frozen=True)
class LivePostgres:
    url: str
    available: bool
    reason: str


def detect() -> LivePostgres:
    url = os.environ.get("THURSDAY_TEST_DATABASE_URL", "")
    if not url:
        return LivePostgres("", False, "THURSDAY_TEST_DATABASE_URL is not set")

    try:
        import asyncio

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError as exc:  # pragma: no cover - asyncpg missing is its own answer
        return LivePostgres(url, False, f"the async driver is missing: {exc}")

    async def probe() -> str:
        engine = create_async_engine(url, future=True)
        try:
            async with engine.begin() as conn:
                found = (
                    await conn.execute(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                    )
                ).scalar_one_or_none()
                if not found:
                    return "the server has no pgvector extension"
                # Reachable and extended is not the same as ready: a database nobody has
                # migrated answers every question here with a missing-relation error, and
                # five of those read as a broken suite rather than an unprepared server.
                migrated = (
                    await conn.execute(text("SELECT to_regclass('public.memories')"))
                ).scalar_one_or_none()
                return "" if migrated else "the database has not been migrated"
        finally:
            await engine.dispose()

    try:
        return LivePostgres(url, not (why := asyncio.run(probe())), why or "reachable")
    except Exception as exc:
        return LivePostgres(url, False, f"{type(exc).__name__}: {str(exc)[:80]}")


live = detect()
