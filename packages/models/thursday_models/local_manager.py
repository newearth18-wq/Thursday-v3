"""LocalModelManager (ADDENDUM §41) — what this machine can run, and what it is doing.

§41 lists nine functions. Six are here: discover_runtimes, discover_models, health,
profile, load state and the capability set that falls out of them. Three are **deliberately
absent** — `install_model`, `remove_model` and `load_model`/`unload_model` as write
operations — and their absence is the design rather than a gap in it.

§39 says a model download must show the model, its size, its source and the disk it needs
before anything is fetched, and §41 puts install and remove behind approval. Approval in
this system means the Permission Engine, and the Permission Engine authorises *actions in
the catalogue*, not method calls on a manager. So install and remove will arrive as device
actions (`ai.model.install`, `ai.model.remove`) with policies of their own, the same way
every other consequential verb did. Adding them here first, guarded by a flag, would create
the one thing §31 forbids: a path to a consequential act that does not pass the engine.

What this class does own is the honest inventory: which runtimes answer, what they hold,
what the hardware underneath them is, and how loaded it is right now.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.compute import (
    ComputeLoad,
    ComputeProfile,
    ModelDescriptor,
    RuntimeKind,
    capabilities_for,
)

from thursday_models.runtimes import AIRuntime, discover

log = get_logger(__name__)


class LocalModelManager:
    """One machine's AI inventory. Lives on the node, reports to the core."""

    def __init__(
        self,
        *,
        configured: dict[RuntimeKind, str] | None = None,
        client: Any = None,
    ) -> None:
        self._configured = configured or {}
        self._client = client
        self._runtimes: list[AIRuntime] = []
        self._models: list[ModelDescriptor] = []

    async def refresh(self) -> list[ModelDescriptor]:
        """Re-read what is installed. Cheap enough to run at startup and on demand.

        Failures are per-runtime: one server that is up but broken must not hide the models
        on a server that is fine, which is what a single try around the whole loop would do.
        """
        self._runtimes = await discover(configured=self._configured, client=self._client)
        models: list[ModelDescriptor] = []
        for runtime in self._runtimes:
            try:
                models.extend(await runtime.models())
            except Exception as exc:
                log.warning("runtime_listing_failed", runtime=str(runtime.kind), error=str(exc))
        self._models = models
        log.info("local_models_discovered", count=len(models))
        return models

    @property
    def models(self) -> list[ModelDescriptor]:
        return list(self._models)

    @property
    def runtimes(self) -> list[AIRuntime]:
        return list(self._runtimes)

    def capabilities(self) -> set[str]:
        """§42. Derived from what is installed, never configured independently of it."""
        return capabilities_for(self._models)

    # ------------------------------------------------------------------ the hardware

    def profile(self) -> ComputeProfile:
        """What this machine has. Every probe degrades rather than raising.

        A node that refuses to start because it could not read its own VRAM is a node that
        does nothing at all, on a machine that can still open a file and run a command.
        """
        profile = ComputeProfile(platform=f"{platform.system()} {platform.machine()}")
        try:
            import psutil

            profile.cpu_cores = psutil.cpu_count(logical=True) or 0
            profile.ram_bytes = psutil.virtual_memory().total
            profile.disk_free_bytes = psutil.disk_usage(str(Path.home())).free
        except Exception as exc:
            log.debug("compute_profile_partial", error=str(exc))

        name, vram = _gpu()
        profile.gpu_name = name
        profile.vram_bytes = vram
        return profile

    def load(self) -> ComputeLoad:
        """§18, §51. What it is doing now. Also never raises."""
        load = ComputeLoad()
        try:
            import psutil

            load.cpu_percent = psutil.cpu_percent(interval=None)
            load.ram_free_bytes = psutil.virtual_memory().available
            if (battery := psutil.sensors_battery()) is not None:
                load.on_battery = not battery.power_plugged
                load.battery_percent = battery.percent
        except Exception as exc:
            log.debug("compute_load_partial", error=str(exc))

        used, free = _gpu_load()
        load.gpu_percent = used
        load.vram_free_bytes = free
        return load


def _gpu() -> tuple[str, int]:
    """Name and total VRAM, or ("", 0) on a machine without a discrete GPU.

    Shells out to `nvidia-smi` because it is the one interface present on every machine that
    has the hardware, without adding a dependency that only helps on some of them. A missing
    binary is the common case and reads as "no GPU", which is the correct answer for the
    router: it compares VRAM, and zero VRAM is never chosen for work that needs some.
    """
    return _nvidia_smi("name,memory.total", parse=lambda parts: (parts[0], _mib(parts[1])))


def _gpu_load() -> tuple[float, int]:
    used, free = _nvidia_smi(
        "utilization.gpu,memory.free",
        parse=lambda parts: (float(parts[0].replace("%", "").strip() or 0), _mib(parts[1])),
        default=(0.0, 0),
    )
    return used, free


def _nvidia_smi(fields: str, *, parse: Any, default: Any = ("", 0)) -> Any:
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return default
    try:
        out = subprocess.run(  # noqa: S603 - a fixed binary found on PATH, no shell, no input
            [binary, f"--query-gpu={fields}", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        first = out.stdout.strip().splitlines()[0]
        return parse([p.strip() for p in first.split(",")])
    except Exception as exc:
        log.debug("nvidia_smi_unreadable", error=str(exc))
        return default


def _mib(text: str) -> int:
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) * 1024 * 1024 if digits else 0
