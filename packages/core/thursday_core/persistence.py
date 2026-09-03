"""Making state survive a restart (Sprint 51).

The schema was designed, the migrations were written, CI ran them from empty on every commit
— and nothing read or wrote through any of it. Memory, tasks and the audit log lived in the
process, so restarting Thursday made it forget everything the owner had told it. Sprint 47's
backup made that recoverable by hand; it did not make it not happen.

The shape here follows ADR 0001, and one decision inside it matters more than the rest.

**The table is the truth; the dict is an index.** Each manager keeps its in-memory structure
because recall walks it on every turn and a database round trip per candidate would be absurd.
But that structure is loaded *from* the table at startup and written *through* to it on every
change — it is a cache, never a second store. Two stores that can disagree are worse than one
store and no persistence, because the disagreement is invisible and the wrong one wins at
random.

**A failed write fails the operation.** `remember` that returns a record it did not persist is
a lie the owner finds out about after a restart. `SqlRepository.put` raises, the manager
raises, and the caller hears about it while there is still something to be done.

**No database is a supported configuration.** `NullRepository` is the offline adapter, and it
is not a degraded mode: the whole test suite runs on it, `python -m apps.cli` runs on it, and
a deployment that wants a purely ephemeral assistant gets one. What it does not get is a
quiet promise of durability it will not keep — `Container.persistent` says which it is.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from thursday_core.logging import get_logger

log = get_logger(__name__)


class Repository(Protocol):
    """Where one kind of record is kept between runs."""

    async def load(self) -> list[dict]: ...

    async def put(self, row: dict) -> None: ...

    async def remove(self, key: UUID | str) -> None: ...

    async def clear(self) -> None: ...


class NullRepository:
    """The offline adapter: state lives for as long as the process does.

    Deliberately not a warning-logging stub. Running without a database is a supported
    configuration — every test does it — and a component that complained on every write would
    train people to ignore it. What must not happen is a *silent* claim of durability, and
    that is `Container.persistent`'s job rather than this class's.
    """

    async def load(self) -> list[dict]:
        return []

    async def put(self, row: dict) -> None:
        return None

    async def remove(self, key: UUID | str) -> None:
        return None

    async def clear(self) -> None:
        return None


class SqlRepository:
    """One ORM table, addressed as rows of plain data.

    Generic over the model because the ORM and domain shapes were designed together and
    differ by two or three fields. Fields the table does not have are dropped rather than
    raising: `MemoryRecord.score` is a retrieval artifact, not state, and a repository that
    refused to store a record because the query layer decorated it would be enforcing a rule
    nobody wrote.

    The reverse — a *column* the domain model does not know about — is filled by `defaults`,
    which is where `user_id` comes from. Thursday is single-tenant; the column exists because
    the schema was drawn for a world where it might not be.
    """

    def __init__(
        self,
        model: Any,
        *,
        session_scope: Any,
        defaults: dict[str, Any] | None = None,
        order_by: str | None = None,
        fields: set[str] | None = None,
    ) -> None:
        self._model = model
        self._scope = session_scope
        self._defaults = defaults or {}
        self._order_by = order_by
        self._columns = {c.name for c in model.__table__.columns}
        #: What the domain model will accept back. The mapping has to work in both
        #: directions: a table carries columns the domain type has no field for — `user_id`,
        #: `updated_at` — and handing those back makes a strict model reject the row it just
        #: wrote. A repository that only maps outward is a repository that cannot load.
        self._fields = fields or self._columns

    async def load(self) -> list[dict]:
        from sqlalchemy import select

        statement = select(self._model)
        if self._order_by is not None:
            # Order matters for anything whose meaning depends on sequence — the audit chain
            # most of all. Loading "all the rows" in whatever order the engine returns them
            # would verify a chain that is not the chain that was written.
            statement = statement.order_by(getattr(self._model, self._order_by))

        async with self._scope() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [self._to_dict(row) for row in rows]

    async def put(self, row: dict) -> None:
        """Insert or update one record.

        Raises on failure, and the caller is expected to let it propagate. A repository that
        swallowed a write error would leave the in-memory index holding something the table
        does not, which is exactly the disagreement this module exists to prevent.
        """
        values = {**self._defaults, **{k: v for k, v in row.items() if k in self._columns}}
        identifier = values.get("id")
        if identifier is None:
            raise ValueError(f"cannot persist a {self._model.__name__} row with no id")

        async with self._scope() as session:
            existing = await session.get(self._model, identifier)
            if existing is None:
                session.add(self._model(**values))
            else:
                for column, value in values.items():
                    setattr(existing, column, value)

    async def remove(self, key: UUID | str) -> None:
        async with self._scope() as session:
            existing = await session.get(self._model, key)
            if existing is not None:
                await session.delete(existing)

    async def clear(self) -> None:
        from sqlalchemy import delete

        async with self._scope() as session:
            await session.execute(delete(self._model))

    def _to_dict(self, row: Any) -> dict:
        return {
            column: _aware(getattr(row, column))
            for column in self._columns
            if column in self._fields
        }


def _aware(value: Any) -> Any:
    """Give a datetime back its timezone.

    SQLite has no timezone type, so a `DateTime(timezone=True)` column round-trips as a naive
    value. Everything downstream compares against `datetime.now(UTC)` — memory decay, recall
    scoring, expiry — and naive-versus-aware arithmetic raises `TypeError`. The effect was a
    restored memory that crashed the first `recall` after a restart, which is a worse failure
    than not persisting at all: it breaks a working system at the moment persistence was
    supposed to help it.

    Assuming UTC is correct rather than convenient: everything written here was written as
    UTC, and §2 makes every stored timestamp UTC ISO-8601 in the first place.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
