"""Vector stores.

``InMemoryVectorStore`` is a brute-force cosine scan — correct, dependency-free, and fast
enough for a personal corpus. ``PgVectorStore`` is the production path; the port is the
same so the Memory Manager never learns which one it has.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger

from thursday_memory.embeddings import comparable, cosine

log = get_logger(__name__)


class InMemoryVectorStore:
    name = "memory"

    def __init__(self) -> None:
        self._items: dict[UUID, tuple[list[float], dict[str, Any]]] = {}

    async def upsert(self, items: Sequence[tuple[UUID, list[float], dict[str, Any]]]) -> None:
        for item_id, vector, meta in items:
            self._items[item_id] = (list(vector), dict(meta))

    async def search(
        self, vector: list[float], *, k: int = 8, where: dict[str, Any] | None = None
    ) -> list[tuple[UUID, float]]:
        """Nearest by cosine, skipping anything this query cannot be compared with.

        A stored vector of a different width is *unjudgeable*, not unrelated, and the two
        used to be the same answer: it came back as a hit scoring 0.0, which reads exactly
        like "considered and found irrelevant". This is a search whose entire output is a
        similarity, so returning a number that is not one is the wrong kind of wrong —
        `unreadable` says how many were left out instead.
        """
        where = where or {}
        scored: list[tuple[UUID, float]] = []
        skipped = 0
        for item_id, (stored, meta) in self._items.items():
            if any(meta.get(key) != value for key, value in where.items()):
                continue
            if not comparable(vector, stored):
                skipped += 1
                continue
            scored.append((item_id, cosine(vector, stored)))
        if skipped:
            log.warning("vector_search_skipped_unreadable", skipped=skipped, width=len(vector))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def unreadable(self, width: int) -> int:
        """How many stored vectors a query of this width cannot be compared with."""
        return sum(1 for stored, _ in self._items.values() if not comparable([0.0] * width, stored))

    def widths(self) -> dict[int, int]:
        """How many stored vectors there are of each width. One key is the healthy case."""
        counts: dict[int, int] = {}
        for stored, _ in self._items.values():
            counts[len(stored)] = counts.get(len(stored), 0) + 1
        return counts

    async def delete(self, ids: Sequence[UUID]) -> None:
        for item_id in ids:
            self._items.pop(item_id, None)

    def __len__(self) -> int:
        return len(self._items)


class PgVectorStore:
    """pgvector-backed store. Requires the ``vector`` extension and an HNSW index.

    The table name is a constructor argument fixed by the container, never user input;
    every value is bound as a parameter. Hence the targeted ``noqa: S608`` below.
    """

    name = "pgvector"

    def __init__(
        self, session_factory: Any, *, table: str = "memories", dimensions: int = 768
    ) -> None:
        self._session_factory = session_factory
        self._table = table
        self._dimensions = dimensions

    async def upsert(self, items: Sequence[tuple[UUID, list[float], dict[str, Any]]]) -> None:
        from sqlalchemy import text

        # `_dimensions` was assigned in `__init__` and never read once, which is how a store
        # that knows exactly how wide its column is came to write whatever it was handed.
        # Postgres answers this with an error at insert time and SQLite does not answer at
        # all, so the check belongs here, where both behave the same.
        for item_id, vector, _meta in items:
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"memory {item_id} has a {len(vector)}-dimension embedding and this "
                    f"store's column holds {self._dimensions} — refusing to write a vector "
                    "that could never be compared with the others"
                )

        async with self._session_factory() as session:
            for item_id, vector, _meta in items:
                await session.execute(
                    text(f"UPDATE {self._table} SET embedding = :v WHERE id = :id"),  # noqa: S608
                    {"v": list(vector), "id": item_id},
                )
            await session.commit()

    async def search(
        self, vector: list[float], *, k: int = 8, where: dict[str, Any] | None = None
    ) -> list[tuple[UUID, float]]:
        from sqlalchemy import text

        clauses = " AND ".join(f"{key} = :{key}" for key in (where or {}))
        predicate = f"WHERE {clauses}" if clauses else ""
        # The table name is fixed by construction; every value is a bound parameter.
        query = text(
            f"SELECT id, 1 - (embedding <=> :v) AS score FROM {self._table} "  # noqa: S608
            f"{predicate} ORDER BY embedding <=> :v LIMIT :k"
        )
        async with self._session_factory() as session:
            rows = await session.execute(query, {"v": list(vector), "k": k, **(where or {})})
            return [(row.id, float(row.score)) for row in rows]

    async def delete(self, ids: Sequence[UUID]) -> None:
        from sqlalchemy import text

        async with self._session_factory() as session:
            await session.execute(
                text(f"UPDATE {self._table} SET embedding = NULL WHERE id = ANY(:ids)"),  # noqa: S608
                {"ids": list(ids)},
            )
            await session.commit()
