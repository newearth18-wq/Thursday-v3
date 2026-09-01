"""FakeDeviceNode (PART 88).

A device node that behaves like a real one without touching a real machine: it models
state, and verification reads that state back. Shipped rather than confined to the test
suite, because three groups need it —

* automated tests, which must not depend on Chrome being installed;
* the CLI's ``--fake-device`` mode, for trying Thursday on a machine you would rather it
  did not touch;
* anyone extending the node protocol, who needs a node they can make misbehave on demand.

``fail_launch`` is the important one: it makes a command report success while nothing
actually starts, which is the exact case PART 5.1 exists for. A test double that can only
succeed cannot test the property the system is built around.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from thursday_shared.models import DeviceCapabilities

from thursday_devices.node.adapters.base import OSAdapter
from thursday_devices.node.executor import NodeExecutor


class FakeAdapter(OSAdapter):
    """A machine that exists only in memory."""

    os_name = "FakeOS"

    def __init__(
        self,
        *,
        fail_launch: bool = False,
        slow_by: float = 0.0,
        offline_after: int | None = None,
        capabilities: DeviceCapabilities | None = None,
    ) -> None:
        #: The launch "works" but no process appears — dispatch without effect.
        self.fail_launch = fail_launch
        #: Added to every action, for exercising timeouts.
        self.slow_by = slow_by
        #: Start failing every action after this many, for exercising a node dying mid-task.
        self.offline_after = offline_after

        self.running: dict[str, int] = {}
        self.window: str | None = None
        self.clipboard = ""
        self.volume = 0.5
        self.notifications: list[tuple[str, str]] = []
        self.opened_urls: list[str] = []
        self.shell_commands: list[str] = []
        self.action_count = 0
        self._next_pid = 1000
        self._capabilities = capabilities or DeviceCapabilities.of(
            "app.open",
            "app.close",
            "file.open",
            "file.read",
            "file.write",
            "file.delete",
            "file.search",
            "system.info",
            "system.process.list",
            "system.process.start",
            "system.process.stop",
            "system.power",
            "window.active",
            "clipboard.read",
            "clipboard.write",
            "notify.show",
            "audio.volume",
            "audio.speaker",
            "shell.run",
            "browser.open",
        )

    # -------------------------------------------------------------- capabilities

    def capabilities(self) -> DeviceCapabilities:
        return self._capabilities

    def can_read_window(self) -> bool:
        return True

    def can_clipboard(self) -> bool:
        return True

    def can_notify(self) -> bool:
        return True

    def can_volume(self) -> bool:
        return True

    # -------------------------------------------------------------- behaviour

    async def _tick(self) -> None:
        self.action_count += 1
        if self.offline_after is not None and self.action_count > self.offline_after:
            raise ConnectionError("the fake device went offline")
        if self.slow_by:
            await asyncio.sleep(self.slow_by)

    def resolve_executable(self, name: str) -> str | None:
        return f"/usr/bin/{name}"

    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        await self._tick()
        self._next_pid += 1
        if self.fail_launch:
            # The command "succeeded" and nothing started. This is the case PART 5.1 is for.
            return {"pid": self._next_pid, "executable": name}
        self.running[name] = self._next_pid
        self.window = f"{name} — window"
        return {"pid": self._next_pid, "executable": name}

    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        pid = self.running.get(name)
        return [{"pid": pid, "name": name}] if pid else []

    async def terminate(self, name: str, *, force: bool = False) -> dict[str, Any]:
        await self._tick()
        pid = self.running.pop(name, None)
        return {"terminated": [pid] if pid else []}

    async def active_window(self) -> str | None:
        return self.window

    async def open_path(self, path: str) -> dict[str, Any]:
        await self._tick()
        self._next_pid += 1
        self.window = f"{Path(path).name} — viewer"
        return {"pid": self._next_pid, "path": path}

    async def open_url(self, url: str) -> dict[str, Any]:
        await self._tick()
        self.opened_urls.append(url)
        self.window = f"{url} — browser"
        return {"opened": True}

    async def clipboard_get(self) -> str:
        return self.clipboard

    async def clipboard_set(self, text: str) -> None:
        await self._tick()
        self.clipboard = text

    async def notify(self, title: str, body: str) -> None:
        await self._tick()
        self.notifications.append((title, body))

    async def get_volume(self) -> float:
        return self.volume

    async def set_volume(self, level: float) -> None:
        await self._tick()
        self.volume = level

    async def run_shell(self, command: str, *, timeout: float = 30.0) -> dict[str, Any]:
        await self._tick()
        self.shell_commands.append(command)
        return {"exit_code": 0, "stdout": f"ran: {command}", "stderr": ""}


class FakeDeviceNode:
    """A ready-made node: a fake machine plus the real executor.

    The executor is genuine — path confinement, argument validation and verification all
    run exactly as they do on a real node. Only the machine underneath is imaginary.
    """

    def __init__(
        self,
        *,
        name: str = "Fake-PC",
        allowed_roots: list[Path] | None = None,
        **adapter_options: Any,
    ) -> None:
        self.name = name
        self.adapter = FakeAdapter(**adapter_options)
        self.executor = NodeExecutor(self.adapter, allowed_roots=allowed_roots or [Path.home()])

    def session(self, *, device_id: Any = None, kind: str = "desktop") -> Any:
        """A hub session for this node, connected the same way a loopback node is."""
        from thursday_shared.ids import new_id

        from thursday_devices.hub import LoopbackDeviceSession

        return LoopbackDeviceSession(
            device_id=device_id or new_id(),
            name=self.name,
            kind=kind,
            executor=self.executor,
        )
