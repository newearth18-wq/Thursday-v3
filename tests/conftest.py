"""Test fixtures.

Everything runs with zero infrastructure: no Postgres, no Redis, no model credentials, no
GUI. That is a design requirement, not a convenience — a system whose safety properties can
only be tested against production is a system whose safety properties are not tested.

``FakeAdapter`` is imported from ``thursday_devices.fake`` rather than defined here: it is
a shipped artifact (PART 88), and a private copy in the test suite would drift from the one
the CLI and the protocol's own users rely on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container
from thursday_devices.fake import FakeAdapter
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.ids import new_id

__all__ = ["FakeAdapter"]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "var",
        obsidian_vault=tmp_path / "vault",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/thursday.db",
        log_level="WARNING",
        llm_backend="rule",
        vault_backend="memory",
        approval_ttl_seconds=2.0,
    )


@pytest.fixture
def container(settings: Settings):
    return build_container(settings, configure_logs=False)


@pytest.fixture
def adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
async def office_pc(container, adapter, tmp_path):
    """A connected device node, wired through the same hub path a remote node uses."""
    device_id = new_id()
    session = LoopbackDeviceSession(
        device_id=device_id,
        name="Office-PC",
        executor=NodeExecutor(adapter, allowed_roots=[tmp_path]),
    )
    await container.hub.register(session, location_context="office")
    container.world.update(active_device_id=device_id, active_device_name="Office-PC")
    return session


@pytest.fixture
def session_id():
    return new_id()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
