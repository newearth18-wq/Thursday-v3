"""Measuring models from the work they actually do (ADDENDUM §25, §26) — Sprint 61.

§25 asks for tokens/sec, time-to-first-token, latency, throughput and success rate, and says
what they are for in four words: *use real data to adjust routing*. §26 adds success history —
model A succeeds 96% of the time at this kind of work, model B 82%, so prefer A when quality
matters.

Measurement that feeds routing has a failure mode that measurement for a dashboard does not:
**a bad number changes which model runs next, and the model that does not run is never
measured again.** Every decision here is about that loop.

**Measured from real work, not from a synthetic benchmark.** A benchmark prompt measures a
prompt nobody asked for, on a machine in a state nobody was in. Real calls are already
happening and already have a stopwatch on them, so this records those. The cost is that a
model nobody uses stays unmeasured — which is honest, and which the router already handles by
reading "unmeasured" as unknown rather than as slow.

**A cold model is not a slow model.** The first call after a model is paged in from disk
measures the disk. §22 already tracks LOADED/UNLOADED, so cold samples are recorded and kept
out of the speed figure: a 40-second first token would otherwise make a good model look
unusable for as long as the window remembers it.

**The median, not the mean.** One sample taken during a backup, a thermal event or a
suspend/resume is enough to move a mean by an order of magnitude and route work away
permanently. A median needs half the samples to be bad before it moves.

**A failure is only the model's if the model failed.** The machine being unplugged, the
socket dropping, the owner disabling the model mid-flight — none of those are evidence about
the model, and counting them against its success rate would let one bad afternoon on the
network permanently demote the best model in the house. Faults are classified, and only
model-attributable ones count.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5

from thursday_shared.compute import ModelState

from thursday_core.logging import get_logger
from thursday_core.persistence import NullRepository

log = get_logger(__name__)

#: How many samples are kept per model. Bounded so a long-running core does not grow without
#: limit, and long enough that a median means something.
WINDOW = 50

#: Below this, the figures are reported and not trusted for routing. Two samples that agree
#: are a coincidence; a handful that agree are a measurement.
MIN_SAMPLES = 5

#: Samples older than this are dropped. Hardware changes, models are re-quantised, drivers are
#: updated — a number from six weeks ago describes a machine that may no longer exist.
MAX_AGE = timedelta(days=14)


class Fault(StrEnum):
    """Whose fault a failure was — the question §26's success rate depends on."""

    #: The model ran and produced something unusable: malformed output, refused schema, a
    #: verdict the supervisor rejected. This is evidence about the model.
    MODEL = "model"
    #: The machine, the socket, the runtime, the owner disabling it mid-flight. Evidence
    #: about the deployment, and none at all about the model.
    INFRASTRUCTURE = "infrastructure"
    #: Not classified. Counted in neither direction, because guessing would put the guess
    #: into a number that decides routing.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Sample:
    """One real call, measured."""

    at: datetime
    latency_ms: int
    ok: bool
    tokens_out: int = 0
    ttft_ms: int | None = None
    fault: Fault = Fault.UNKNOWN
    #: §22. A call served by a model that had to be paged in first measures the disk.
    cold: bool = False

    @property
    def tokens_per_second(self) -> float | None:
        if not self.ok or self.tokens_out <= 0 or self.latency_ms <= 0:
            return None
        return self.tokens_out / (self.latency_ms / 1000)


@dataclass
class BenchmarkProfile:
    """What the samples say about one model (§25)."""

    key: str
    samples: deque[Sample] = field(default_factory=lambda: deque(maxlen=WINDOW))
    #: The hardware these samples describe. Empty until the first sample names it.
    fingerprint: str = ""

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    def fresh(self, *, now: datetime | None = None) -> list[Sample]:
        cutoff = (now or datetime.now(UTC)) - MAX_AGE
        return [s for s in self.samples if s.at > cutoff]

    @property
    def warm_speeds(self) -> list[float]:
        return [
            tps for s in self.fresh() if not s.cold and (tps := s.tokens_per_second) is not None
        ]

    @property
    def tokens_per_second(self) -> float:
        """Median throughput on warm calls, or 0.0 when it has not been measured enough.

        Zero means *unmeasured*, and the router reads it that way (ADR 0046). Returning a
        provisional number from two samples would be worse than returning none: the router
        cannot tell a guess from a measurement, so it must not be given one.
        """
        speeds = self.warm_speeds
        return round(statistics.median(speeds), 2) if len(speeds) >= MIN_SAMPLES else 0.0

    @property
    def time_to_first_token_ms(self) -> float:
        values = [s.ttft_ms for s in self.fresh() if not s.cold and s.ttft_ms is not None]
        return round(statistics.median(values), 1) if len(values) >= MIN_SAMPLES else 0.0

    @property
    def latency_ms(self) -> float:
        values = [s.latency_ms for s in self.fresh() if not s.cold]
        return round(statistics.median(values), 1) if values else 0.0

    @property
    def success_rate(self) -> float:
        """§26. Successes over calls the model could have got right.

        Infrastructure failures are excluded from both halves rather than counted as
        successes: a model that was never reached neither succeeded nor failed, and putting
        those in either column would make a flaky network look like a model problem or hide
        a real one behind an unreachable machine.
        """
        judged = [s for s in self.fresh() if s.fault is not Fault.INFRASTRUCTURE]
        if len(judged) < MIN_SAMPLES:
            return 0.0
        return round(sum(1 for s in judged if s.ok) / len(judged), 3)

    @property
    def measured(self) -> bool:
        """Whether there is enough here for the router to lean on."""
        return len(self.warm_speeds) >= MIN_SAMPLES

    @property
    def infrastructure_failures(self) -> int:
        return sum(1 for s in self.fresh() if s.fault is Fault.INFRASTRUCTURE)

    def report(self) -> dict:
        return {
            "key": self.key,
            "samples": len(self.fresh()),
            "measured": self.measured,
            "tokens_per_second": self.tokens_per_second,
            "time_to_first_token_ms": self.time_to_first_token_ms,
            "latency_ms": self.latency_ms,
            "success_rate": self.success_rate,
            "infrastructure_failures": self.infrastructure_failures,
        }


def key_for(device_id: UUID | None, model: str) -> str:
    """One profile per (machine, model). The same model is not the same thing on a 4090 and
    on a laptop, and averaging them would describe neither."""
    return f"{device_id or 'cloud'}|{model}"


#: What a measurement was taken on, when Thursday cannot see the machine at all. A provider
#: runs its own hardware and changes it without telling anybody, so there is nothing here to
#: compare against — the freshness window is the only bound on a stale cloud measurement, and
#: this constant exists to say that rather than to imply a check that is not happening.
CLOUD_FINGERPRINT = "cloud"


def fingerprint_of(profile: Any) -> str:
    """How a machine describes the hardware a measurement would be taken on.

    Only the parts that change what a model does: the GPU and the memory it has to work
    with. Deliberately *not* the hostname or the device id — renaming a machine does not make
    last week's throughput wrong, and the device id is already half the key.

    An unknown profile is its own value rather than falling back to `CLOUD_FINGERPRINT`: a
    local machine that has not reported its hardware yet is not a provider, and letting the
    two share a string would make a measurement taken before the report survive a swap.
    """
    if profile is None:
        return "unreported"
    return "|".join(
        (
            str(getattr(profile, "gpu_name", "") or ""),
            str(getattr(profile, "vram_bytes", 0) or 0),
            str(getattr(profile, "ram_bytes", 0) or 0),
            str(getattr(profile, "cpu_cores", 0) or 0),
        )
    )


class BenchmarkBook:
    """Every model's measurements, and the place real calls report into."""

    def __init__(self, *, repository: Any = None, hub: Any = None) -> None:
        self._profiles: dict[str, BenchmarkProfile] = {}
        #: Keys changed since the last flush.
        self._dirty: set[str] = set()
        #: Sprint 103. Until then this was assigned and never read once — a persistence hook
        #: that persisted nothing, under a fourteen-day freshness window on data that could
        #: not survive a restart. The window had therefore never applied.
        self._repository = repository or NullRepository()
        #: Where a machine's description of its own hardware comes from. Optional: without
        #: it every local measurement is stamped "unreported", which restores only onto
        #: another machine that has also not reported — never onto a known, different one.
        self._hub = hub

    # ------------------------------------------------------------------ hardware identity

    def fingerprint(self, device_id: UUID | None) -> str:
        """How the machine behind `device_id` currently describes its hardware."""
        if device_id is None:
            return CLOUD_FINGERPRINT
        summary = self._hub.summary(device_id) if self._hub else None
        return fingerprint_of(getattr(summary, "compute", None))

    def record(
        self,
        *,
        device_id: UUID | None,
        model: str,
        latency_ms: int,
        ok: bool = True,
        tokens_out: int = 0,
        ttft_ms: int | None = None,
        fault: Fault = Fault.UNKNOWN,
        state: ModelState = ModelState.LOADED,
        at: datetime | None = None,
    ) -> Sample:
        """Record one real call. Called by whatever ran it, not by a benchmark harness."""
        sample = Sample(
            at=at or datetime.now(UTC),
            latency_ms=max(0, latency_ms),
            ok=ok,
            tokens_out=max(0, tokens_out),
            ttft_ms=ttft_ms,
            fault=fault if not ok else Fault.UNKNOWN,
            cold=state is not ModelState.LOADED,
        )
        key = key_for(device_id, model)
        profile = self._profiles.setdefault(key, BenchmarkProfile(key=key))
        # Stamped at record time, from the machine as it describes itself now. Taking it at
        # restore time instead would compare a measurement against whatever hardware happens
        # to be there later, which is the comparison, not the fact being compared.
        profile.fingerprint = self.fingerprint(device_id)
        profile.add(sample)
        self._dirty.add(key)
        return sample

    # ------------------------------------------------------------------ between runs

    async def flush(self) -> int:
        """Write through what changed since the last flush.

        Not called from `record`, which is synchronous and on the path of every model call.
        A measurement lost to a crash between flushes costs a sample out of fifty; an await
        per inference costs every call.
        """
        written = 0
        for key in sorted(self._dirty):
            profile = self._profiles.get(key)
            if profile is None:
                continue
            await self._repository.put(_row_of(profile))
            written += 1
        self._dirty.clear()
        return written

    async def restore(self, *, now: datetime | None = None) -> int:
        """Load what was kept, discarding what no longer describes anything real.

        Two reasons a stored profile is dropped, and they are different questions:

        * **Its samples aged out.** The window was always fourteen days; until this existed
          nothing lived long enough to reach it.
        * **The hardware changed.** This is the decision [§23](../../23-release-readiness.md)
          said had to be made before benchmarks could be persisted at all — whether a
          measurement taken before a hardware change should outlive it. It should not, and
          the machine can say so without anybody guessing: a measurement describes a model
          on particular hardware, so it is kept while that hardware still answers to the same
          description and discarded when it does not.

        A cloud profile is never dropped for hardware, because there is none to compare.
        """
        restored = 0
        for row in await self._repository.load():
            key = str(row.get("key") or "")
            if not key:
                continue
            stored = str(row.get("fingerprint") or "")
            device_id, _ = _split_key(key)
            if stored != self.fingerprint(device_id):
                log.info(
                    "benchmark_discarded_hardware_changed",
                    key=key,
                    measured_on=stored,
                    now=self.fingerprint(device_id),
                )
                await self._repository.remove(row_id_for(key))
                continue
            samples = [s for s in (_sample_of(r) for r in row.get("samples") or []) if s]
            profile = BenchmarkProfile(key=key, fingerprint=stored)
            cutoff = (now or datetime.now(UTC)) - MAX_AGE
            for sample in sorted(samples, key=lambda s: s.at):
                if sample.at > cutoff:
                    profile.add(sample)
            if not profile.samples:
                # Nothing inside the window. Removed rather than kept as an empty row, or
                # the table accumulates a permanent record of every model ever tried.
                await self._repository.remove(row_id_for(key))
                continue
            self._profiles[key] = profile
            restored += 1
        log.info("benchmarks_restored", profiles=restored)
        return restored

    def profile(self, device_id: UUID | None, model: str) -> BenchmarkProfile:
        key = key_for(device_id, model)
        return self._profiles.setdefault(key, BenchmarkProfile(key=key))

    def speed_of(self, device_id: UUID | None, model: str) -> float:
        """What the router asks. Zero means unmeasured, never slow."""
        return self.profile(device_id, model).tokens_per_second

    def success_of(self, device_id: UUID | None, model: str) -> float:
        return self.profile(device_id, model).success_rate

    def all(self) -> list[BenchmarkProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.key)

    def report(self) -> dict:
        profiles = self.all()
        return {
            "models": [p.report() for p in profiles],
            "measured": sum(1 for p in profiles if p.measured),
            "unmeasured": sum(1 for p in profiles if not p.measured),
        }

    def __len__(self) -> int:
        return len(self._profiles)


# --------------------------------------------------------------------------- serialisation


def _split_key(key: str) -> tuple[UUID | None, str]:
    """The inverse of `key_for`. Split once from the left: a model name may contain `|`."""
    where, _, model = key.partition("|")
    if where == CLOUD_FINGERPRINT:
        return None, model
    try:
        return UUID(where), model
    except ValueError:
        return None, model


#: Namespace for turning a profile key into a stable row id. The house repository addresses
#: rows by primary key, and a profile's identity is its `device|model` string — so the id is
#: derived from it rather than generated, and the same profile always rewrites its own row
#: instead of adding another one on every flush.
_ROW_NAMESPACE = UUID("6f1a5b6e-1d4e-5a2c-9f77-0b3a51c9d842")


def row_id_for(key: str) -> UUID:
    return uuid5(_ROW_NAMESPACE, key)


def _row_of(profile: BenchmarkProfile) -> dict:
    device_id, model = _split_key(profile.key)
    samples = list(profile.samples)
    return {
        "id": row_id_for(profile.key),
        "key": profile.key,
        "device_id": device_id,
        "model_name": model,
        "fingerprint": profile.fingerprint,
        "samples": [
            {
                "at": s.at.isoformat(),
                "latency_ms": s.latency_ms,
                "ok": s.ok,
                "tokens_out": s.tokens_out,
                "ttft_ms": s.ttft_ms,
                "fault": str(s.fault),
                "cold": s.cold,
            }
            for s in samples
        ],
        "last_benchmarked_at": max((s.at for s in samples), default=None),
    }


def _sample_of(raw: Any) -> Sample | None:
    """One stored sample, or None when the row cannot be read.

    A single unreadable sample is dropped rather than failing the whole restore: losing one
    measurement out of fifty is a rounding error, and refusing to start because of it would
    turn a bad row into an outage.
    """
    if not isinstance(raw, dict):
        return None
    try:
        at = datetime.fromisoformat(str(raw["at"]))
        return Sample(
            at=at if at.tzinfo else at.replace(tzinfo=UTC),
            latency_ms=int(raw.get("latency_ms") or 0),
            ok=bool(raw.get("ok", True)),
            tokens_out=int(raw.get("tokens_out") or 0),
            ttft_ms=int(raw["ttft_ms"]) if raw.get("ttft_ms") is not None else None,
            fault=Fault(str(raw.get("fault") or Fault.UNKNOWN)),
            cold=bool(raw.get("cold", False)),
        )
    except (KeyError, TypeError, ValueError):
        log.warning("benchmark_sample_unreadable")
        return None
