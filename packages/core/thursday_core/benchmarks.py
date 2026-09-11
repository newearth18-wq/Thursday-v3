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
from uuid import UUID

from thursday_shared.compute import ModelState

from thursday_core.logging import get_logger

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


class BenchmarkBook:
    """Every model's measurements, and the place real calls report into."""

    def __init__(self, *, repository: Any = None) -> None:
        self._profiles: dict[str, BenchmarkProfile] = {}
        self._repository = repository

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
        self._profiles.setdefault(key, BenchmarkProfile(key=key)).add(sample)
        return sample

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
