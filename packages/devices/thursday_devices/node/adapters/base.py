"""The OS adapter contract.

Adapters do two things per action: perform it, and then *observe* whether it happened.
The observation is what the core turns into ``verified`` — never the absence of an
exception (§20).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.models import DeviceCapabilities, DeviceTelemetry

log = get_logger(__name__)


class OSAdapter(ABC):
    """Cross-platform behaviour lives here; subclasses override only what differs."""

    os_name = "unknown"

    # -------------------------------------------------------------- capabilities

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
            screenshot=self.can_screenshot(),
            read_active_window=self.can_read_window(),
            clipboard=self.can_clipboard(),
            notify=self.can_notify(),
            volume=self.can_volume(),
            process_status=True,
            system_info=True,
            power=True,
            speaker=True,
        )

    def can_screenshot(self) -> bool:
        return False

    def can_read_window(self) -> bool:
        return False

    def can_clipboard(self) -> bool:
        return False

    def can_notify(self) -> bool:
        return False

    def can_volume(self) -> bool:
        return False

    async def telemetry(self) -> DeviceTelemetry:
        telemetry = DeviceTelemetry(
            current_user=os.environ.get("USER") or os.environ.get("USERNAME")
        )
        try:
            import psutil

            telemetry.cpu_percent = psutil.cpu_percent(interval=None)
            telemetry.memory_percent = psutil.virtual_memory().percent
            telemetry.disk_free_gb = round(psutil.disk_usage(str(Path.home())).free / 2**30, 1)
            if (battery := psutil.sensors_battery()) is not None:
                telemetry.battery_percent = battery.percent
                telemetry.charging = battery.power_plugged
        except Exception as exc:
            log.debug("telemetry_partial", error=str(exc))
        telemetry.active_window = await self.active_window()
        return telemetry

    # -------------------------------------------------------------- applications

    @abstractmethod
    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        """Start an application. Returns evidence, e.g. ``{"pid": 1234}``."""

    @abstractmethod
    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        """Processes matching ``name`` — the observation half of launch/close."""

    async def terminate(self, name: str, *, force: bool = False) -> dict[str, Any]:
        killed = []
        for process in await self.find_processes(name):
            try:
                import psutil

                handle = psutil.Process(process["pid"])
                handle.kill() if force else handle.terminate()
                killed.append(process["pid"])
            except Exception as exc:
                log.debug("terminate_skipped", pid=process.get("pid"), error=str(exc))
                continue
        return {"terminated": killed}

    def resolve_executable(self, name: str) -> str | None:
        """Map a spoken app name to something the OS can start."""
        return shutil.which(name)

    async def active_window(self) -> str | None:
        return None

    # -------------------------------------------------------------- shell

    async def run_shell(self, command: str, *, timeout: float = 30.0) -> dict[str, Any]:
        process = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            raise
        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode(errors="replace")[:8000],
            "stderr": stderr.decode(errors="replace")[:4000],
        }

    # -------------------------------------------------------------- files

    async def open_path(self, path: str) -> dict[str, Any]:
        raise NotImplementedError

    async def screenshot(self, **kwargs: Any) -> bytes:
        raise NotImplementedError

    async def clipboard_get(self) -> str:
        raise NotImplementedError

    async def clipboard_set(self, text: str) -> None:
        raise NotImplementedError

    async def notify(self, title: str, body: str) -> None:
        raise NotImplementedError

    async def get_volume(self) -> float:
        raise NotImplementedError

    async def set_volume(self, level: float) -> None:
        raise NotImplementedError
