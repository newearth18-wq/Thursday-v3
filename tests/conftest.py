"""Test fixtures.

Everything runs with zero infrastructure: no Postgres, no Redis, no model credentials, no
GUI. That is a design requirement, not a convenience — a system whose safety properties can
only be tested against production is a system whose safety properties are not tested.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from thursday.core.config import Settings
from thursday.core.container import build_container
from thursday.devices.hub import LoopbackDeviceSession
from thursday.devices.node.adapters.base import OSAdapter
from thursday.devices.node.executor import NodeExecutor
from thursday.shared.ids import new_id
from thursday.shared.models import DeviceCapabilities


class FakeAdapter(OSAdapter):
    """A deterministic stand-in for a real machine.

    It models the one thing that matters for the vertical slice: an action changes observable
    state, and verification reads that state back. ``fail_launch`` lets a test make the
    launch *appear* to succeed while the process never shows up — which is exactly the case
    §76 is about.
    """

    os_name = "FakeOS"

    def __init__(self, *, fail_launch: bool = False) -> None:
        self.running: dict[str, int] = {}
        self.fail_launch = fail_launch
        self.window: str | None = None
        self.clipboard = ""
        self.volume = 0.5
        self.notifications: list[tuple[str, str]] = []
        self._next_pid = 1000

    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            open_app=True,
            close_app=True,
            open_file=True,
            write_file=True,
            delete_file=True,
            list_dir=True,
            search_files=True,
            run_shell=True,
            process_status=True,
            system_info=True,
            read_active_window=True,
            clipboard=True,
            notify=True,
            volume=True,
            speaker=True,
        )

    def can_read_window(self) -> bool:
        return True

    def can_clipboard(self) -> bool:
        return True

    def can_notify(self) -> bool:
        return True

    def can_volume(self) -> bool:
        return True

    def resolve_executable(self, name: str) -> str | None:
        return f"/usr/bin/{name}"

    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        self._next_pid += 1
        if self.fail_launch:
            # The command "succeeded" but nothing actually started.
            return {"pid": self._next_pid, "executable": name}
        self.running[name] = self._next_pid
        self.window = f"{name} — window"
        return {"pid": self._next_pid, "executable": name}

    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        pid = self.running.get(name)
        return [{"pid": pid, "name": name}] if pid else []

    async def terminate(self, name: str, *, force: bool = False) -> dict[str, Any]:
        pid = self.running.pop(name, None)
        return {"terminated": [pid] if pid else []}

    async def active_window(self) -> str | None:
        return self.window

    async def open_path(self, path: str) -> dict[str, Any]:
        self._next_pid += 1
        self.window = f"{Path(path).name} — viewer"
        return {"pid": self._next_pid, "path": path}

    async def clipboard_get(self) -> str:
        return self.clipboard

    async def clipboard_set(self, text: str) -> None:
        self.clipboard = text

    async def notify(self, title: str, body: str) -> None:
        self.notifications.append((title, body))

    async def get_volume(self) -> float:
        return self.volume

    async def set_volume(self, level: float) -> None:
        self.volume = level

    async def run_shell(self, command: str, *, timeout: float = 30.0) -> dict[str, Any]:
        return {"exit_code": 0, "stdout": f"ran: {command}", "stderr": ""}


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
