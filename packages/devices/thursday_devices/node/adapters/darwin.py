"""macOS adapter. Uses ``open`` and AppleScript — application integration before OS APIs."""

from __future__ import annotations

import asyncio
import shlex
import tempfile
from pathlib import Path
from typing import Any

from thursday_devices.node.adapters.base import OSAdapter

_ALIASES: dict[str, str] = {
    "chrome": "Google Chrome",
    "firefox": "Firefox",
    "notepad": "TextEdit",
    "calc": "Calculator",
    "excel": "Microsoft Excel",
    "winword": "Microsoft Word",
    "terminal": "Terminal",
    "explorer": "Finder",
    "code": "Visual Studio Code",
    "obsidian": "Obsidian",
}


class DarwinAdapter(OSAdapter):
    os_name = "Darwin"

    def can_screenshot(self) -> bool:
        return True

    def can_read_window(self) -> bool:
        return True

    def can_clipboard(self) -> bool:
        return True

    def can_notify(self) -> bool:
        return True

    def can_volume(self) -> bool:
        return True

    def resolve_executable(self, name: str) -> str | None:
        return _ALIASES.get(name.lower(), name)

    async def _osascript(self, script: str, *, timeout: float = 15.0) -> str:
        process = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace").strip())
        return stdout.decode(errors="replace").strip()

    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        app = self.resolve_executable(name) or name
        extra = " ".join(shlex.quote(a) for a in (args or []))
        result = await self.run_shell(f"open -a {shlex.quote(app)} {extra}".strip(), timeout=20)
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"].strip() or f"failed to open {app}")
        processes = await self.find_processes(app)
        return {"pid": processes[0]["pid"] if processes else None, "executable": app}

    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        app = self.resolve_executable(name) or name
        result = await self.run_shell(f"pgrep -f {shlex.quote(app)}", timeout=5)
        return [
            {"pid": int(line), "name": app} for line in result["stdout"].split() if line.isdigit()
        ]

    async def active_window(self) -> str | None:
        try:
            return await self._osascript(
                'tell application "System Events" to get name of first application process '
                "whose frontmost is true"
            )
        except Exception:
            return None

    async def open_path(self, path: str) -> dict[str, Any]:
        target = Path(path).expanduser()
        if not await asyncio.to_thread(target.exists):
            raise FileNotFoundError(path)
        await self.run_shell(f"open {shlex.quote(str(target))}", timeout=15)
        return {"path": str(target)}

    async def screenshot(self, **kwargs: Any) -> bytes:
        target = Path(tempfile.gettempdir()) / "thursday-shot.png"
        await self.run_shell(f"screencapture -x {target}", timeout=20)

        def read() -> bytes:
            data = target.read_bytes()
            target.unlink(missing_ok=True)
            return data

        return await asyncio.to_thread(read)

    async def clipboard_get(self) -> str:
        return (await self.run_shell("pbpaste", timeout=5))["stdout"]

    async def clipboard_set(self, text: str) -> None:
        await self.run_shell(f"printf %s {shlex.quote(text)} | pbcopy", timeout=5)

    async def notify(self, title: str, body: str) -> None:
        await self._osascript(f"display notification {body!r} with title {title!r}")

    async def get_volume(self) -> float:
        return float(await self._osascript("output volume of (get volume settings)")) / 100

    async def set_volume(self, level: float) -> None:
        await self._osascript(f"set volume output volume {max(0, min(100, round(level * 100)))}")
