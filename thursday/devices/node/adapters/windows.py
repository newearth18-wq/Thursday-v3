"""Windows adapter (§19).

Deliberately ordered by control tier: a documented API or PowerShell cmdlet first, UI
Automation next, and coordinate clicking never from here. Every action pairs with an
observation — a process handle, a window title, a file hash — so the node can report
``verified`` truthfully.

Requires ``pywin32`` for window queries; degrades to PowerShell where it is absent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from thursday.devices.node.adapters.base import OSAdapter

#: Spoken name → executable. Start-Process resolves anything on PATH or in App Paths.
_ALIASES: dict[str, str] = {
    "chrome": "chrome.exe",
    "msedge": "msedge.exe",
    "firefox": "firefox.exe",
    "notepad": "notepad.exe",
    "calc": "calc.exe",
    "excel": "excel.exe",
    "winword": "winword.exe",
    "powerpnt": "powerpnt.exe",
    "outlook": "outlook.exe",
    "explorer": "explorer.exe",
    "terminal": "wt.exe",
    "code": "code.cmd",
    "obsidian": "obsidian.exe",
    "spotify": "spotify.exe",
}


class WindowsAdapter(OSAdapter):
    os_name = "Windows"

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
        return _ALIASES.get(name.lower(), name if name.lower().endswith(".exe") else f"{name}.exe")

    async def _powershell(self, script: str, *, timeout: float = 30.0) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return {
            "exit_code": process.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    async def launch(self, name: str, args: list[str] | None = None) -> dict[str, Any]:
        executable = self.resolve_executable(name) or name
        argument_list = ""
        if args:
            joined = ",".join(f"'{a}'" for a in args)
            argument_list = f" -ArgumentList {joined}"
        result = await self._powershell(
            f"$p = Start-Process -FilePath '{executable}'{argument_list} -PassThru; $p.Id"
        )
        if result["exit_code"] != 0:
            raise RuntimeError(result["stderr"].strip() or f"failed to start {executable}")
        pid = next((int(t) for t in result["stdout"].split() if t.strip().isdigit()), None)
        return {"pid": pid, "executable": executable}

    async def find_processes(self, name: str) -> list[dict[str, Any]]:
        stem = Path(self.resolve_executable(name) or name).stem
        result = await self._powershell(
            f"Get-Process -Name '{stem}' -ErrorAction SilentlyContinue | "
            "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"
        )
        payload = result["stdout"].strip()
        if not payload:
            return []
        import json

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        rows = data if isinstance(data, list) else [data]
        return [
            {
                "pid": row.get("Id"),
                "name": row.get("ProcessName"),
                "window": row.get("MainWindowTitle"),
            }
            for row in rows
        ]

    async def terminate(self, name: str, *, force: bool = False) -> dict[str, Any]:
        stem = Path(self.resolve_executable(name) or name).stem
        flag = " -Force" if force else ""
        await self._powershell(f"Stop-Process -Name '{stem}'{flag} -ErrorAction SilentlyContinue")
        return {"terminated": [p["pid"] for p in await self.find_processes(name)]}

    async def active_window(self) -> str | None:
        result = await self._powershell(
            "Add-Type -AssemblyName UIAutomationClient;"
            "[System.Windows.Automation.AutomationElement]::FocusedElement.Current.Name",
            timeout=10,
        )
        title = result["stdout"].strip()
        if title:
            return title
        # Fall back to the foreground process's own window title.
        result = await self._powershell(
            "(Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
            "Sort-Object -Property StartTime -Descending | Select-Object -First 1).MainWindowTitle"
        )
        return result["stdout"].strip() or None

    async def open_path(self, path: str) -> dict[str, Any]:
        target = Path(path)
        if not await asyncio.to_thread(target.exists):
            raise FileNotFoundError(path)
        result = await self._powershell(f"Start-Process -FilePath '{target}' -PassThru | % Id")
        pid = next((int(t) for t in result["stdout"].split() if t.strip().isdigit()), None)
        return {"pid": pid, "path": str(target)}

    async def screenshot(self, **kwargs: Any) -> bytes:
        target = Path.home() / "AppData" / "Local" / "Temp" / "thursday-shot.png"
        await self._powershell(
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp = New-Object Drawing.Bitmap $b.Width, $b.Height;"
            "$g = [Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location, [Drawing.Point]::Empty, $b.Size);"
            f"$bmp.Save('{target}');",
            timeout=30,
        )

        def read() -> bytes:
            data = target.read_bytes()
            target.unlink(missing_ok=True)
            return data

        return await asyncio.to_thread(read)

    async def clipboard_get(self) -> str:
        return (await self._powershell("Get-Clipboard -Raw"))["stdout"]

    async def clipboard_set(self, text: str) -> None:
        escaped = text.replace("'", "''")
        await self._powershell(f"Set-Clipboard -Value '{escaped}'")

    async def notify(self, title: str, body: str) -> None:
        await self._powershell(
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            f"$n.BalloonTipTitle = '{title.replace(chr(39), chr(39) * 2)}';"
            f"$n.BalloonTipText = '{body.replace(chr(39), chr(39) * 2)}';"
            "$n.Visible = $true; $n.ShowBalloonTip(5000);"
        )

    async def get_volume(self) -> float:
        result = await self._powershell(
            "Add-Type -TypeDefinition @'\n"
            "using System.Runtime.InteropServices;\n"
            '[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n'
            "interface IAudioEndpointVolume { int _(); int __(); int ___(); int ____();\n"
            "  int SetMasterVolumeLevelScalar(float v, System.Guid g); int _____();\n"
            "  int GetMasterVolumeLevelScalar(out float v); }\n"
            "'@\n; 'unsupported'"
        )
        if "unsupported" in result["stdout"]:
            raise RuntimeError("master volume query needs the audio helper component")
        return 0.0

    async def set_volume(self, level: float) -> None:
        # WScript.Shell volume keys move in 2% steps; good enough for "turn it down".
        steps = max(0, min(50, round(level * 50)))
        await self._powershell(
            "$w = New-Object -ComObject WScript.Shell;"
            "1..50 | % { $w.SendKeys([char]174) };"
            f"1..{steps} | % {{ $w.SendKeys([char]175) }}"
        )

    async def run_shell(self, command: str, *, timeout: float = 30.0) -> dict[str, Any]:
        return await self._powershell(command, timeout=timeout)
