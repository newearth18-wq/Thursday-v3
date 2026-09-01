"""Linux adapter. Works on X11 and (partially) Wayland; degrades honestly when a tool is
absent rather than reporting success it cannot observe.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from thursday.devices.node.adapters.base import OSAdapter

#: Spoken names → the binaries that are actually likely to exist on a Linux desktop.
_ALIASES: dict[str, list[str]] = {
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "firefox": ["firefox"],
    "code": ["code", "codium"],
    "terminal": ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"],
    "explorer": ["nautilus", "dolphin", "thunar"],
    "calc": ["gnome-calculator", "kcalc", "xcalc"],
    "notepad": ["gedit", "kate", "mousepad"],
    "excel": ["localc", "libreoffice"],
    "winword": ["lowriter", "libreoffice"],
    "obsidian": ["obsidian"],
}


class LinuxAdapter(OSAdapter):
    os_name = "Linux"

    def can_screenshot(self) -> bool:
        return any(shutil.which(t) for t in ("gnome-screenshot", "scrot", "import", "grim"))

    def can_read_window(self) -> bool:
        return bool(shutil.which("xdotool"))

    def can_clipboard(self) -> bool:
        return any(shutil.which(t) for t in ("xclip", "xsel", "wl-copy"))

    def can_notify(self) -> bool:
        return bool(shutil.which("notify-send"))

    def can_volume(self) -> bool:
        return any(shutil.which(t) for t in ("pactl", "amixer"))

    def resolve_executable(self, name: str) -> str | None:
        for candidate in _ALIASES.get(name.lower(), [name]):
            if path := shutil.which(candidate):
                return path
        return None

    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        executable = self.resolve_executable(name)
        if executable is None:
            raise FileNotFoundError(f"no executable found for {name!r} on this machine")
        process = await asyncio.create_subprocess_exec(
            executable,
            *(args or []),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"pid": process.pid, "executable": executable}

    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        candidates = {c.lower() for c in _ALIASES.get(name.lower(), [name])} | {name.lower()}
        found: list[dict[str, Any]] = []
        try:
            import psutil

            for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
                info = process.info
                haystack = " ".join(
                    filter(
                        None,
                        [
                            info.get("name") or "",
                            info.get("exe") or "",
                            " ".join(info.get("cmdline") or []),
                        ],
                    )
                ).lower()
                if any(candidate in haystack for candidate in candidates):
                    found.append({"pid": info["pid"], "name": info.get("name")})
        except ImportError:
            result = await self.run_shell(f"pgrep -a {name}", timeout=5)
            for line in result["stdout"].splitlines():
                pid, _, command = line.partition(" ")
                if pid.isdigit():
                    found.append({"pid": int(pid), "name": command})
        return found

    async def active_window(self) -> str | None:
        if not shutil.which("xdotool") or not os.environ.get("DISPLAY"):
            return None
        try:
            result = await self.run_shell("xdotool getactivewindow getwindowname", timeout=3)
            return result["stdout"].strip() or None
        except Exception:
            return None

    async def open_path(self, path: str) -> dict[str, Any]:
        target = Path(path).expanduser()
        if not await asyncio.to_thread(target.exists):
            raise FileNotFoundError(path)
        opener = shutil.which("xdg-open")
        if opener is None:
            raise RuntimeError("xdg-open is not available on this machine")
        process = await asyncio.create_subprocess_exec(
            opener,
            str(target),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return {"pid": process.pid, "path": str(target)}

    async def screenshot(self, **kwargs: Any) -> bytes:
        import asyncio as _asyncio

        for tool, command in (
            ("gnome-screenshot", "gnome-screenshot -f {path}"),
            ("scrot", "scrot {path}"),
            ("import", "import -window root {path}"),
            ("grim", "grim {path}"),
        ):
            if shutil.which(tool):
                target = Path(tempfile.gettempdir()) / f"thursday-shot-{os.getpid()}.png"
                await self.run_shell(command.format(path=target), timeout=15)
                return await _asyncio.to_thread(_read_and_remove, target)
        raise RuntimeError("no screenshot tool is installed on this machine")

    async def clipboard_get(self) -> str:
        for command in ("xclip -selection clipboard -o", "xsel --clipboard --output", "wl-paste"):
            if shutil.which(command.split()[0]):
                return (await self.run_shell(command, timeout=5))["stdout"]
        raise RuntimeError("no clipboard tool is installed")

    async def clipboard_set(self, text: str) -> None:
        import shlex

        quoted = shlex.quote(text)
        for command in (
            f"printf %s {quoted} | xclip -selection clipboard",
            f"printf %s {quoted} | xsel --clipboard --input",
            f"printf %s {quoted} | wl-copy",
        ):
            if shutil.which(command.split("|")[1].strip().split()[0]):
                await self.run_shell(command, timeout=5)
                return
        raise RuntimeError("no clipboard tool is installed")

    async def notify(self, title: str, body: str) -> None:
        import shlex

        if not shutil.which("notify-send"):
            raise RuntimeError("notify-send is not installed")
        await self.run_shell(f"notify-send {shlex.quote(title)} {shlex.quote(body)}", timeout=5)

    async def get_volume(self) -> float:
        if shutil.which("pactl"):
            result = await self.run_shell("pactl get-sink-volume @DEFAULT_SINK@", timeout=5)
            for token in result["stdout"].split():
                if token.endswith("%"):
                    return float(token.rstrip("%")) / 100
        raise RuntimeError("no volume control available")

    async def set_volume(self, level: float) -> None:
        percent = max(0, min(100, round(level * 100)))
        if shutil.which("pactl"):
            await self.run_shell(f"pactl set-sink-volume @DEFAULT_SINK@ {percent}%", timeout=5)
            return
        raise RuntimeError("no volume control available")


def _read_and_remove(path: Path) -> bytes:
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data
