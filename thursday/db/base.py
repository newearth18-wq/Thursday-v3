"""Declarative base and portable column types.

The same models run on PostgreSQL (production) and SQLite (development and CI). Where the
two differ — UUIDs, JSON, vectors — a portable type decorator picks the native form on
Postgres and a faithful fallback on SQLite, so tests exercise the real schema.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    """UUID on Postgres, 36-character string on SQLite."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONColumn(TypeDecorator):
    """JSONB on Postgres (indexable), TEXT-encoded JSON on SQLite."""

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(value, ensure_ascii=False, default=str)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.loads(value)


class Vector(TypeDecorator):
    """pgvector on Postgres, JSON array on SQLite.

    On SQLite the vector store falls back to a brute-force cosine scan, which is correct and
    fast enough for a personal corpus — see ``thursday/memory/vector.py``.
    """

    impl = Text
    cache_ok = True

    def __init__(self, dimensions: int = 768) -> None:
        super().__init__()
        self.dimensions = dimensions

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector as PGVector

                return dialect.type_descriptor(PGVector(self.dimensions))
            except ImportError:  # pragma: no cover - production installs pgvector
                return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return json.dumps(list(value))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return json.loads(value)


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


def json_default() -> dict[str, Any]:
    return {}
