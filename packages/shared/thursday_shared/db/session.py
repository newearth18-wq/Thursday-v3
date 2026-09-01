"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from thursday_core.config import Settings

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _factory
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_pre_ping=not settings.database_url.startswith("sqlite"),
        future=True,
    )
    _factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _factory is None:
        raise RuntimeError("call init_engine(settings) before requesting a session")
    return _factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None
