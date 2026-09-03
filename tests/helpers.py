"""Test helpers that need the real wiring."""

from __future__ import annotations

from pathlib import Path

from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.ids import new_id

from tests.conftest import FakeAdapter


async def connect_failing_node(container, tmp_path: Path):
    """A node whose launch command reports success but whose process never appears."""
    adapter = FakeAdapter(fail_launch=True)
    session = LoopbackDeviceSession(
        device_id=new_id(),
        name="Broken-PC",
        executor=NodeExecutor(adapter, allowed_roots=[tmp_path]),
    )
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Broken-PC")
    return session
