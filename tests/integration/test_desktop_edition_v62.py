"""The desktop edition installs nothing and configures nothing (EASY INSTALL) — Sprint 62.

The requirement's operative sentence: *"Any architectural choice that improves theoretical
flexibility but forces normal users to manually configure infrastructure should be rejected
unless required."* And its acceptance list is about a machine with no Python, no Node, no
Docker and no Ollama.

Two shipped defaults failed that, and both are the kind of failure that survives a thousand
passing tests because the test suite never reads the file a user's install reads:

  · `settings.yaml` set `redis://localhost:6379/0`. The code default was already null and
    the in-process store already existed — but this file is what a fresh install loads, so a
    normal user got `RedisStateStore` and then `ModuleNotFoundError: No module named 'redis'`
    on the first state operation, for a service they never chose to need.

  · every `persist_*` flag was off, so a desktop user told Thursday something, restarted, and
    it had forgotten — with the SQLite file sitting right there, unused.

`Settings.external_services()` exists so "needs nothing" is something code answers rather
than something a README claims.
"""

from __future__ import annotations

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container, start
from thursday_core.state import InMemoryStateStore, build_state_store
from thursday_shared.enums import MemoryLayer
from thursday_shared.models import MemoryWrite


def shipped() -> Settings:
    """Exactly what a fresh install gets: `settings.yaml` and nothing else.

    No fixture, deliberately. The `settings` fixture in conftest overrides half of this to
    keep tests fast and ephemeral — which is right for the other twelve hundred tests and
    exactly wrong here, because the thing under test *is* the shipped configuration.
    """
    return Settings()


# --------------------------------------------------------------------------- needs nothing


def test_a_fresh_install_requires_no_external_service():
    """The whole requirement, as one assertion.

    If this fails, somebody has added a dependency a normal user would have to install, and
    the installer's promise — download, double-click, next, install — is no longer true.
    """
    assert shipped().external_services() == []


def test_the_shipped_configuration_does_not_reach_for_redis():
    """The specific bug. `redis` is not even a declared dependency, so the failure a user
    hit was an import error for a package that was never going to be there."""
    settings = shipped()
    assert settings.redis_url is None
    assert isinstance(build_state_store(settings.redis_url), InMemoryStateStore)


async def test_the_in_process_store_actually_works():
    """Not a stub standing in for Redis: the store a desktop install uses has to work."""
    store = build_state_store(shipped().redis_url)
    await store.set("probe", {"x": 1})
    assert await store.get("probe") == {"x": 1}


def test_the_shipped_database_is_a_file_not_a_server():
    settings = shipped()
    assert settings.uses_postgres is False
    assert settings.resolved_database_url.startswith("sqlite")


def test_redis_is_not_a_dependency_of_the_desktop_edition():
    """A desktop install must not need the package at all. Asserted by importing it and
    expecting failure, because "we do not use it" and "it is not installed" are different
    claims and only the second one survives a user's machine."""
    with pytest.raises(ImportError):
        import redis  # noqa: F401


# --------------------------------------------------------------------------- remembers


def test_a_fresh_install_is_configured_to_remember():
    """The second failure. An assistant that forgets everything when the PC restarts is not
    an assistant, and the storage to prevent it was already there and switched off."""
    settings = shipped()
    assert settings.persist_memory is True
    assert settings.persist_audit is True
    assert settings.persist_tasks is True


async def test_a_desktop_install_actually_keeps_a_memory_across_a_restart(tmp_path):
    """Configured to remember is not the same as remembering. Two containers over one
    SQLite file, which is as close to a restart as a test gets."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from thursday_shared.db import models as _models  # noqa: F401 - registers the tables
    from thursday_shared.db.base import Base

    url = f"sqlite+aiosqlite:///{tmp_path}/thursday.db"
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    def install() -> Settings:
        # The shipped settings, pointed at a temporary file rather than the user's home.
        return Settings(
            database_url=url,
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            log_level="ERROR",
        )

    from thursday_shared.db.session import dispose_engine

    await dispose_engine()
    first = build_container(install(), configure_logs=False)
    await start(first)
    await first.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC, content="the bins go out on Tuesday", importance=0.9
        )
    )

    await dispose_engine()
    second = build_container(install(), configure_logs=False)
    await start(second)

    assert second.persistent is True
    assert any("bins go out on Tuesday" in row["content"] for row in second.memory.export_state())


# --------------------------------------------------------------------------- the editions


def test_the_desktop_edition_is_the_default():
    """Default SIMPLE, per the requirement. A power user opts into the complicated one."""
    assert shipped().edition == "desktop"
    assert shipped().is_desktop is True


def test_a_hub_declares_what_it_needs_rather_than_hiding_it():
    """Thursday Hub earns its dependencies by being multi-process. The point of reporting
    them is that the installer and the health check can say what to start, instead of a
    connection error saying it for them."""
    hub = Settings(
        edition="hub",
        database_url="postgresql+asyncpg://thursday@localhost:5432/thursday",
        redis_url="redis://localhost:6379/0",
    )
    assert hub.is_desktop is False
    assert hub.external_services() == ["PostgreSQL", "Redis"]


def test_the_report_names_each_service_separately():
    """So a health check can say "start Redis" rather than "something is missing"."""
    assert Settings(redis_url="redis://localhost:6379/0").external_services() == ["Redis"]
    assert Settings(database_url="postgresql+asyncpg://t@localhost/t").external_services() == [
        "PostgreSQL"
    ]


# --------------------------------------------------------------------------- it starts


async def test_a_desktop_container_builds_and_starts_with_no_configuration(tmp_path):
    """The end of the install: Thursday opens. No terminal, no environment variables, no
    services — the container builds from the shipped file and comes up."""
    settings = Settings(
        data_dir=tmp_path / "var",
        obsidian_vault=tmp_path / "vault",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/thursday.db",
        log_level="ERROR",
        persist_memory=False,
        persist_audit=False,
        persist_costs=False,
        persist_tasks=False,
        persist_models=False,
    )
    assert settings.external_services() == []

    container = build_container(settings, configure_logs=False)
    await start(container)

    assert container.engine is not None
    assert container.hub is not None
    assert isinstance(container.state, InMemoryStateStore)
