"""Device action catalogue (PART 23).

Each entry declares the capability a node must advertise, the permission level, the risk,
and — crucially — how the effect is *verified*. An action with ``verify=False`` is one whose
success genuinely cannot be observed; there are very few, and they report ``verified=False``
rather than pretending (PART 28).

Names are namespaced (`file.read`, `system.process.stop`). Capabilities are namespaced too,
so a node can advertise `file.*` without enumerating every verb, and the hub can refuse
`file.delete` on a node that advertised only `file.read`. See ADR 0007.
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday_shared.actions import canonical
from thursday_shared.enums import ControlTier, PermissionLevel, RiskLevel


@dataclass(frozen=True)
class ActionSpec:
    name: str
    capability: str
    level: PermissionLevel
    risk: RiskLevel = RiskLevel.LOW
    control_tier: ControlTier = ControlTier.OS_API
    verify: bool = True
    reversible: bool = True
    required_args: tuple[str, ...] = ()
    description: str = ""


CATALOGUE: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        # ---------------------------------------------------------------- applications
        ActionSpec(
            "app.open",
            "app.open",
            PermissionLevel.OPEN,
            required_args=("app",),
            description="launch an application and confirm its process and window",
        ),
        ActionSpec(
            "app.close",
            "app.close",
            PermissionLevel.MODIFY,
            RiskLevel.LOW,
            required_args=("app",),
            description="terminate an application",
        ),
        # ---------------------------------------------------------------- files
        ActionSpec(
            "file.open",
            "file.open",
            PermissionLevel.OPEN,
            required_args=("path",),
            description="open a file with its registered handler",
        ),
        ActionSpec(
            "file.read",
            "file.read",
            PermissionLevel.READ,
            required_args=("path",),
            description="read a text file",
        ),
        ActionSpec(
            "file.write",
            "file.write",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("path", "content"),
            description="write text to a file, backing up any existing version first",
        ),
        ActionSpec(
            "file.create",
            "file.write",
            PermissionLevel.MODIFY,
            required_args=("path",),
            description="create an empty file",
        ),
        ActionSpec(
            "file.folder.create",
            "file.write",
            PermissionLevel.MODIFY,
            required_args=("path",),
            description="create a directory",
        ),
        ActionSpec(
            "file.move",
            "file.write",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("src", "dst"),
            description="move a path",
        ),
        ActionSpec(
            "file.rename",
            "file.write",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("src", "dst"),
            description="rename a path",
        ),
        ActionSpec(
            "file.copy",
            "file.write",
            PermissionLevel.MODIFY,
            required_args=("src", "dst"),
            description="copy a path",
        ),
        ActionSpec(
            "file.delete",
            "file.delete",
            PermissionLevel.MODIFY,
            RiskLevel.HIGH,
            reversible=True,
            required_args=("path",),
            description="delete a path (quarantined, so it stays recoverable)",
        ),
        ActionSpec(
            "file.list",
            "file.read",
            PermissionLevel.READ,
            required_args=("path",),
            verify=False,
            description="list a directory",
        ),
        ActionSpec(
            "file.search",
            "file.search",
            PermissionLevel.READ,
            verify=False,
            required_args=("root", "pattern"),
            description="find files by pattern, newest first",
        ),
        # ---------------------------------------------------------------- screen & window
        ActionSpec(
            "window.active",
            "window.active",
            PermissionLevel.READ,
            verify=False,
            description="report the focused window",
        ),
        ActionSpec(
            "screen.capture",
            "screen.capture",
            PermissionLevel.OPEN,
            RiskLevel.LOW,
            description="capture the screen",
        ),
        # ---------------------------------------------------------------- system
        ActionSpec(
            "system.info",
            "system.info",
            PermissionLevel.READ,
            verify=False,
            description="report OS, CPU, memory and disk",
        ),
        ActionSpec(
            "system.process.list",
            "system.process.list",
            PermissionLevel.READ,
            verify=False,
            required_args=("name",),
            description="check whether a process is running",
        ),
        ActionSpec(
            "system.process.start",
            "app.open",
            PermissionLevel.OPEN,
            required_args=("name",),
            description="start a process by name",
        ),
        ActionSpec(
            "system.process.stop",
            "app.close",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("name",),
            description="stop a process by name",
        ),
        ActionSpec(
            "device.wake",
            "device.wake",
            PermissionLevel.SYSTEM,
            RiskLevel.MEDIUM,
            # Verified, and by the only evidence there is: the machine's node connecting.
            # A magic packet is unacknowledged UDP, so "sent" tells you nothing about
            # whether anything woke (ADDENDUM §20, ADR 0012).
            verify=True,
            reversible=False,
            required_args=("device_id",),
            description="wake a sleeping machine with a magic packet",
        ),
        ActionSpec(
            "system.lock",
            "system.power",
            PermissionLevel.SYSTEM,
            RiskLevel.LOW,
            verify=False,
            description="lock the session",
        ),
        ActionSpec(
            "system.power",
            "system.power",
            PermissionLevel.SYSTEM,
            RiskLevel.HIGH,
            verify=False,
            reversible=False,
            required_args=("mode",),
            description="sleep, restart, or shut down",
        ),
        # ---------------------------------------------------------------- clipboard & audio
        ActionSpec(
            "clipboard.read",
            "clipboard.read",
            PermissionLevel.READ,
            verify=False,
            description="read the clipboard",
        ),
        ActionSpec(
            "clipboard.write",
            "clipboard.write",
            PermissionLevel.MODIFY,
            required_args=("text",),
            description="write to the clipboard",
        ),
        ActionSpec(
            "audio.volume.get",
            "audio.volume",
            PermissionLevel.READ,
            verify=False,
            description="read the output volume",
        ),
        ActionSpec(
            "audio.volume.set",
            "audio.volume",
            PermissionLevel.MODIFY,
            required_args=("level",),
            description="set the output volume",
        ),
        # ---------------------------------------------------------------- shell & misc
        ActionSpec(
            "powershell.run",
            "powershell.run",
            PermissionLevel.MODIFY,
            RiskLevel.HIGH,
            ControlTier.OS_API,
            reversible=False,
            required_args=("command",),
            description="run a PowerShell command",
        ),
        ActionSpec(
            "shell.run",
            "shell.run",
            PermissionLevel.MODIFY,
            RiskLevel.HIGH,
            ControlTier.OS_API,
            reversible=False,
            required_args=("command",),
            description="run a shell command",
        ),
        ActionSpec(
            "browser.open",
            "browser.open",
            PermissionLevel.OPEN,
            required_args=("url",),
            control_tier=ControlTier.BROWSER,
            description="open a URL in the default browser",
        ),
        ActionSpec(
            "notify.show",
            "notify.show",
            PermissionLevel.OPEN,
            verify=False,
            required_args=("title", "body"),
            description="show a desktop notification",
        ),
    )
}


def get(action: str) -> ActionSpec | None:
    return CATALOGUE.get(canonical(action))


def missing_args(action: str, args: dict) -> list[str]:
    spec = get(action)
    if spec is None:
        return []
    return [name for name in spec.required_args if name not in args or args[name] in (None, "")]
