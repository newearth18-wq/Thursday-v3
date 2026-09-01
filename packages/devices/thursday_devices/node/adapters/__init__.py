"""Per-OS adapters. Everything platform-specific lives behind ``OSAdapter``."""

from __future__ import annotations

import platform


def for_current_platform() -> object:
    """Pick the adapter for this machine."""
    system = platform.system()
    if system == "Windows":
        from thursday_devices.node.adapters.windows import WindowsAdapter

        return WindowsAdapter()
    if system == "Darwin":
        from thursday_devices.node.adapters.darwin import DarwinAdapter

        return DarwinAdapter()
    from thursday_devices.node.adapters.linux import LinuxAdapter

    return LinuxAdapter()
