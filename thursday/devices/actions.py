"""Device action catalogue (§9.3).

Each entry declares the capability a node must advertise, the permission level, the risk,
and — crucially — how the effect is *verified*. An action with ``verify=False`` is one whose
success genuinely cannot be observed; there are very few, and they report ``verified=False``
rather than pretending (§20).
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday.shared.enums import ControlTier, PermissionLevel, RiskLevel


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
        ActionSpec(
            "open_app",
            "open_app",
            PermissionLevel.OPEN,
            required_args=("name",),
            description="launch an application and confirm its process and window",
        ),
        ActionSpec(
            "close_app",
            "close_app",
            PermissionLevel.MODIFY,
            RiskLevel.LOW,
            required_args=("name",),
            description="terminate an application",
        ),
        ActionSpec(
            "open_file",
            "open_file",
            PermissionLevel.OPEN,
            required_args=("path",),
            description="open a file with its registered handler",
        ),
        ActionSpec(
            "write_file",
            "write_file",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("path", "content"),
            description="write text to a file",
        ),
        ActionSpec(
            "read_file",
            "list_dir",
            PermissionLevel.READ,
            required_args=("path",),
            description="read a text file",
        ),
        ActionSpec(
            "create_folder",
            "write_file",
            PermissionLevel.MODIFY,
            required_args=("path",),
            description="create a directory",
        ),
        ActionSpec(
            "move",
            "write_file",
            PermissionLevel.MODIFY,
            RiskLevel.MEDIUM,
            required_args=("src", "dst"),
            description="move or rename a path",
        ),
        ActionSpec(
            "copy",
            "write_file",
            PermissionLevel.MODIFY,
            required_args=("src", "dst"),
            description="copy a path",
        ),
        ActionSpec(
            "delete",
            "delete_file",
            PermissionLevel.MODIFY,
            RiskLevel.HIGH,
            reversible=False,
            required_args=("path",),
            description="delete a path (to the recycle bin where available)",
        ),
        ActionSpec(
            "list_dir",
            "list_dir",
            PermissionLevel.READ,
            required_args=("path",),
            verify=False,
            description="list a directory",
        ),
        ActionSpec(
            "search_files",
            "search_files",
            PermissionLevel.READ,
            verify=False,
            required_args=("root", "pattern"),
            description="find files by pattern",
        ),
        ActionSpec(
            "read_active_window",
            "read_active_window",
            PermissionLevel.READ,
            verify=False,
            description="report the focused window",
        ),
        ActionSpec(
            "screenshot",
            "screenshot",
            PermissionLevel.READ,
            RiskLevel.LOW,
            description="capture the screen",
        ),
        ActionSpec(
            "run_shell",
            "run_shell",
            PermissionLevel.MODIFY,
            RiskLevel.HIGH,
            ControlTier.OS_API,
            reversible=False,
            required_args=("command",),
            description="run a shell command",
        ),
        ActionSpec(
            "process_status",
            "process_status",
            PermissionLevel.READ,
            verify=False,
            required_args=("name",),
            description="check whether a process is running",
        ),
        ActionSpec(
            "system_info",
            "system_info",
            PermissionLevel.READ,
            verify=False,
            description="report OS, CPU, memory and disk",
        ),
        ActionSpec(
            "get_volume",
            "volume",
            PermissionLevel.READ,
            verify=False,
            description="read the output volume",
        ),
        ActionSpec(
            "set_volume",
            "volume",
            PermissionLevel.MODIFY,
            required_args=("level",),
            description="set the output volume",
        ),
        ActionSpec(
            "clipboard_get",
            "clipboard",
            PermissionLevel.READ,
            verify=False,
            description="read the clipboard",
        ),
        ActionSpec(
            "clipboard_set",
            "clipboard",
            PermissionLevel.MODIFY,
            required_args=("text",),
            description="write to the clipboard",
        ),
        ActionSpec(
            "notify",
            "notify",
            PermissionLevel.OPEN,
            verify=False,
            required_args=("title", "body"),
            description="show a desktop notification",
        ),
        ActionSpec(
            "lock",
            "power",
            PermissionLevel.SYSTEM,
            RiskLevel.LOW,
            verify=False,
            description="lock the session",
        ),
        ActionSpec(
            "power",
            "power",
            PermissionLevel.SYSTEM,
            RiskLevel.HIGH,
            verify=False,
            reversible=False,
            required_args=("mode",),
            description="sleep, restart, or shut down",
        ),
    )
}


def get(action: str) -> ActionSpec | None:
    return CATALOGUE.get(action)


def missing_args(action: str, args: dict) -> list[str]:
    spec = CATALOGUE.get(action)
    if spec is None:
        return []
    return [name for name in spec.required_args if name not in args or args[name] in (None, "")]
