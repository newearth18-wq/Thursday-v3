"""Compute Router (ADDENDUM §7–§9, §13, §15–§19, §46–§47) — Sprint 56.

The Model Router answers "which tier". This answers the three questions the addendum's final
principle adds: **which machine**, **which model on it**, and **may this data go there at
all**. It sits below the Model Router and above the device layer, and it decides nothing about
*whether* an action is allowed — that is the Permission Engine's, unchanged (§30, §31).

One distinction runs through the whole file, and getting it wrong would be the expensive bug.

**A machine can be unsuitable, or it can be forbidden. These are not the same reason.**

§18 says do not send heavy work to a machine whose VRAM is full or that is thermally
throttling. §10 says a SECRET payload must never reach a cloud provider. Both read as "not
that one", and the obvious implementation makes both scoring inputs — GPU load worth so many
points, privacy worth rather more.

That implementation is broken in a way that is invisible until it matters. Points can be
outvoted. A cloud provider that is fast, idle and cheap accumulates enough of them to beat a
busy local box, and the SECRET document leaves the machine because arithmetic said so. A
privacy rule expressed as a large number is a preference, not a rule.

So exclusions run **first**, as a filter, and produce reasons rather than penalties. Whatever
survives is then scored. Nothing a score can say brings an excluded candidate back.

The same split makes the second-worst failure impossible: an *empty* result is returned as an
empty result. §38 says a routing failure must not be silent, so `choose` raises with the
reasons every candidate was rejected, rather than quietly widening the search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from thursday_shared.compute import ComputeLoad, ComputeProfile, ModelState, RuntimeKind
from thursday_shared.enums import DataSensitivity
from thursday_shared.errors import ThursdayError

from thursday_core.logging import get_logger

log = get_logger(__name__)


class RoutingMode(StrEnum):
    """§15. How far Thursday may reach for compute."""

    AUTO = "AUTO"
    LOCAL_FIRST = "LOCAL_FIRST"
    #: §16. Nothing — text, image, document, audio or memory — goes to a cloud model. When
    #: local capability is insufficient Thursday says so rather than quietly reaching out.
    LOCAL_ONLY = "LOCAL_ONLY"
    CLOUD_FIRST = "CLOUD_FIRST"
    CLOUD_ONLY = "CLOUD_ONLY"


class RoutingProfile(StrEnum):
    """§46–§47. What to optimise for when several machines could do the job."""

    FAST = "FAST"
    BALANCED = "BALANCED"
    QUALITY = "QUALITY"
    PRIVATE = "PRIVATE"
    OFFLINE = "OFFLINE"
    LOW_POWER = "LOW_POWER"


class NoComputeAvailable(ThursdayError):
    """Nothing could run this, and the reasons are attached (§38)."""

    code = "no_compute_available"


#: §18. Above this, a machine is busy enough that adding heavy work makes everything slower.
BUSY_GPU_PERCENT = 90.0
BUSY_QUEUE = 4
#: §19. Below this on battery, only light work. A laptop that dies mid-answer has not helped.
LOW_BATTERY_PERCENT = 25.0


@dataclass(frozen=True)
class ComputeRequest:
    """What the work needs. Deliberately about the *work*, not about a machine."""

    capability: str = "ai.llm"
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    profile: RoutingProfile = RoutingProfile.BALANCED
    mode: RoutingMode = RoutingMode.AUTO
    #: §45. "use the GPU PC", "local only". An explicit instruction, honoured over every
    #: preference — but never over an exclusion: the owner asking for a machine that cannot
    #: hold the model does not make it fit.
    prefer_device: UUID | None = None
    #: Heavy work is what §18's load rules are about. A small classification on a busy box
    #: is fine; a 70B generation is not.
    heavy: bool = False
    #: Set when the network is down, so cloud candidates are excluded rather than tried and
    #: timed out (§38).
    offline: bool = False


@dataclass(frozen=True)
class Candidate:
    """One (machine, model) pair the router could choose."""

    device_id: UUID | None
    model_name: str
    runtime: RuntimeKind
    local: bool
    profile: ComputeProfile | None = None
    load: ComputeLoad | None = None
    state: ModelState = ModelState.UNLOADED
    tokens_per_second: float = 0.0
    required_vram: int = 0
    required_ram: int = 0


@dataclass(frozen=True)
class ExecutionTarget:
    """§8. Where the work goes, why, and what to try if it fails."""

    device_id: UUID | None
    runtime: RuntimeKind
    model: str
    local: bool
    reasons: tuple[str, ...] = ()
    #: Ordered alternatives, already filtered by the same exclusions. A fallback that was
    #: never checked against the privacy rules is a way around them (§57's whole point).
    fallback: tuple[ExecutionTarget, ...] = field(default_factory=tuple)

    def chain(self) -> list[ExecutionTarget]:
        return [self, *self.fallback]


@dataclass(frozen=True)
class Rejection:
    candidate: str
    reason: str


class ComputeRouter:
    """Chooses the machine and the model. Never decides whether the action is allowed."""

    def __init__(self, *, registry: Any = None, hub: Any = None) -> None:
        self._registry = registry
        self._hub = hub

    # ------------------------------------------------------------------ the decision

    def choose(self, request: ComputeRequest, *, cloud: Any = None) -> ExecutionTarget:
        """Pick a target, or raise saying why nothing qualified."""
        candidates = self.candidates(request.capability)
        if cloud is not None:
            candidates.append(cloud)

        allowed: list[Candidate] = []
        rejected: list[Rejection] = []
        for candidate in candidates:
            why = self._excluded(candidate, request)
            if why is None:
                allowed.append(candidate)
            else:
                rejected.append(Rejection(_label(candidate), why))

        if not allowed:
            # §38. Loudly, with the reasons, rather than falling back to something that was
            # excluded for a reason the caller cannot see.
            raise NoComputeAvailable(
                f"nothing available can run {request.capability}",
                capability=request.capability,
                mode=str(request.mode),
                rejected=[f"{r.candidate}: {r.reason}" for r in rejected],
            )

        ranked = sorted(allowed, key=lambda c: self._score(c, request), reverse=True)
        chosen, *rest = ranked
        target = ExecutionTarget(
            device_id=chosen.device_id,
            runtime=chosen.runtime,
            model=chosen.model_name,
            local=chosen.local,
            reasons=self._reasons(chosen, request),
            fallback=tuple(
                ExecutionTarget(
                    device_id=c.device_id,
                    runtime=c.runtime,
                    model=c.model_name,
                    local=c.local,
                    reasons=("fallback",),
                )
                for c in rest
            ),
        )
        log.info(
            "compute_routed",
            capability=request.capability,
            device_id=str(target.device_id) if target.device_id else "cloud",
            model=target.model,
            local=target.local,
            fallbacks=len(target.fallback),
        )
        return target

    def candidates(self, capability: str) -> list[Candidate]:
        """Every local (machine, model) pair that could serve this capability."""
        if self._registry is None:
            return []
        found: list[Candidate] = []
        for entry in self._registry.for_capability(capability):
            summary = self._hub.summary(entry.device_id) if self._hub else None
            found.append(
                Candidate(
                    device_id=entry.device_id,
                    model_name=entry.name,
                    runtime=entry.observed.runtime,
                    local=True,
                    profile=getattr(summary, "compute", None),
                    load=getattr(summary, "load", None),
                    state=entry.observed.state,
                    tokens_per_second=entry.observed.tokens_per_second,
                    required_vram=entry.observed.required_vram_bytes,
                    required_ram=entry.observed.required_ram_bytes,
                )
            )
        return found

    # ------------------------------------------------------------------ the filter

    def _excluded(self, candidate: Candidate, request: ComputeRequest) -> str | None:
        """Why this candidate cannot be used, or None if it can.

        Every rule here is absolute. Scoring happens afterwards and cannot undo any of it,
        which is the difference between "we prefer local for private data" and "private data
        does not leave the machine".
        """
        # -- privacy and mode: the rules that must never be outvoted -------------
        if not candidate.local:
            if request.sensitivity >= DataSensitivity.SECRET:
                return "SECRET content never reaches a cloud provider (§10)"
            if request.sensitivity >= DataSensitivity.HIGHLY_PRIVATE:
                return "HIGHLY_PRIVATE content is kept local (§32, §93)"
            if request.mode is RoutingMode.LOCAL_ONLY:
                return "LOCAL_ONLY: nothing is sent to a cloud model (§16)"
            if request.profile in (RoutingProfile.PRIVATE, RoutingProfile.OFFLINE):
                return f"the {request.profile} profile does not use cloud models (§47)"
            if request.offline:
                return "the network is unavailable (§38)"
        elif request.mode is RoutingMode.CLOUD_ONLY:
            return "CLOUD_ONLY: local models are not used"

        # -- capacity: the model has to fit at all (§3, §5) ----------------------
        if candidate.profile is not None:
            if candidate.required_vram and candidate.required_vram > candidate.profile.vram_bytes:
                return "not enough VRAM for this model"
            if candidate.required_ram and candidate.required_ram > candidate.profile.ram_bytes:
                return "not enough RAM for this model"

        # -- load and power: §18 and §19, and only for work heavy enough to matter
        load = candidate.load
        if load is not None and request.heavy:
            if load.thermal_throttling:
                return "the machine is thermally throttling (§18)"
            if load.gpu_percent >= BUSY_GPU_PERCENT:
                return f"GPU is at {load.gpu_percent:.0f}% (§18)"
            if load.queue_depth >= BUSY_QUEUE:
                return f"{load.queue_depth} jobs already queued (§18)"
            # `vram_free_bytes` of zero means the node did not report it, not that the
            # card is full — a machine with no GPU reports zero for both.
            wants = candidate.required_vram
            if wants and load.vram_free_bytes and wants > load.vram_free_bytes:
                return "not enough VRAM free right now (§18)"
            if load.on_battery:
                if request.profile is RoutingProfile.LOW_POWER:
                    return "LOW_POWER: no heavy inference on battery (§47)"
                if (load.battery_percent or 100.0) < LOW_BATTERY_PERCENT:
                    return f"on battery at {load.battery_percent:.0f}% (§19)"
        return None

    # ------------------------------------------------------------------ the preference

    def _score(self, candidate: Candidate, request: ComputeRequest) -> tuple:
        """Rank what survived. A tuple, so the ordering is readable rather than a magic sum.

        Higher is better, left to right. Nothing here can resurrect an excluded candidate,
        which is why these can be plain preferences without being dangerous.
        """
        explicit = (
            1 if request.prefer_device and candidate.device_id == request.prefer_device else 0
        )

        local_first = {
            RoutingMode.LOCAL_FIRST: 1,
            RoutingMode.LOCAL_ONLY: 1,
            RoutingMode.CLOUD_FIRST: 0,
        }.get(request.mode, 1 if request.profile is RoutingProfile.PRIVATE else 0)
        locality = local_first if candidate.local else 1 - local_first

        gpu = 1 if (candidate.profile and candidate.profile.has_gpu) else 0
        # §22. A loaded model answers now; an unloaded one may take a minute to page in.
        warm = 1 if candidate.state is ModelState.LOADED else 0
        idle = -(candidate.load.gpu_percent if candidate.load else 0.0)
        # Zero means never measured (§25 has not run), and that must read as unknown rather
        # than as slow — otherwise a benchmarked mediocre model beats an unmeasured good one
        # for ever, because the good one is never chosen and so never measured.
        speed = candidate.tokens_per_second

        if request.profile is RoutingProfile.FAST:
            return (explicit, warm, speed, gpu, locality, idle)
        if request.profile is RoutingProfile.QUALITY:
            return (explicit, gpu, locality, speed, warm, idle)
        if request.profile is RoutingProfile.LOW_POWER:
            plugged = 0 if (candidate.load and candidate.load.on_battery) else 1
            return (explicit, plugged, locality, warm, idle, speed)
        return (explicit, locality, gpu, warm, idle, speed)

    def _reasons(self, candidate: Candidate, request: ComputeRequest) -> tuple[str, ...]:
        reasons = [f"{request.profile} profile"]
        if request.prefer_device and candidate.device_id == request.prefer_device:
            reasons.append("the owner asked for this machine (§45)")
        if candidate.local:
            reasons.append("local inference")
        if candidate.profile and candidate.profile.has_gpu:
            reasons.append(f"GPU available ({candidate.profile.gpu_name or 'discrete'})")
        if candidate.state is ModelState.LOADED:
            reasons.append("model already loaded (§22)")
        if request.sensitivity >= DataSensitivity.HIGHLY_PRIVATE:
            reasons.append(f"{request.sensitivity.name} content stays local")
        return tuple(reasons)


def _label(candidate: Candidate) -> str:
    where = str(candidate.device_id) if candidate.device_id else "cloud"
    return f"{candidate.model_name}@{where}"
