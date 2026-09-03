"""Spatial memory (§25, §26).

"Where are my keys?" is answered from *observations* — label, confidence, place, time —
never from stored video. Frames are not retained by default (§96), and an answer is always
framed as a last sighting, never as a guarantee about the present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.ids import new_id

log = get_logger(__name__)

#: Observations are metadata, but they are still a record of the owner's home. Short by default.
DEFAULT_RETENTION_DAYS = 7


@dataclass
class Observation:
    id: UUID = field(default_factory=new_id)
    label: str = ""
    #: A coarse grouping ("book", "person", "furniture") so "where are my books" works
    #: without the owner naming each one.
    object_type: str = "object"
    confidence: float = 0.0
    location_context: str | None = None
    device_id: UUID | None = None
    #: Which camera saw it. "The office camera saw your keys" is a different claim from
    #: "a camera saw your keys", and the difference is not recoverable afterwards.
    camera_id: str | None = None
    bbox: dict[str, float] = field(default_factory=dict)
    seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    @property
    def position(self) -> tuple[float, float] | None:
        """Where in the frame, as a centre point. None when no box was recorded."""
        if not self.bbox:
            return None
        return (
            self.bbox.get("x", 0.0) + self.bbox.get("width", 0.0) / 2,
            self.bbox.get("y", 0.0) + self.bbox.get("height", 0.0) / 2,
        )

    def describe(self, language: str = "th") -> str:
        """Always phrased as a sighting, with its time and confidence attached."""
        where = self.location_context or ("ที่ไม่ระบุ" if language == "th" else "an unspecified place")
        if language == "th":
            return (
                f"ครั้งล่าสุดที่ระบบเห็น {self.label} คือที่{where} "
                f"เวลา {self.seen_at:%H:%M} (ความมั่นใจ {self.confidence:.2f}) — "
                "ยังไม่ยืนยันว่าตอนนี้ยังอยู่ตรงนั้น"
            )
        return (
            f"{self.label} was last seen at {where} at {self.seen_at:%H:%M} "
            f"(confidence {self.confidence:.2f}). That is a sighting, not a guarantee."
        )


@dataclass
class TrackedObject:
    """One thing, across every time it was seen."""

    label: str
    object_type: str = "object"
    camera_id: str | None = None
    location_context: str | None = None
    position: tuple[float, float] | None = None
    confidence: float = 0.0
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    sightings: int = 0

    def describe(self, language: str = "th", *, now: datetime | None = None) -> str:
        """Always a sighting, never a guarantee — and the *age* of it said out loud.

        "Last seen three days ago" and "last seen a minute ago" are the same sentence
        structurally and completely different answers in practice.
        """
        now = now or datetime.now(UTC)
        hours = (now - self.last_seen).total_seconds() / 3600
        where = self.location_context or ("ที่ไม่ระบุ" if language == "th" else "an unspecified place")
        if language == "th":
            ago = f"{hours:.0f} ชั่วโมงที่แล้ว" if hours >= 1 else "ไม่ถึงชั่วโมงที่แล้ว"
            return (
                f"เห็น {self.label} ครั้งล่าสุดที่{where} {ago} "
                f"(ความมั่นใจ {self.confidence:.0%}) — ยังไม่ยืนยันว่าตอนนี้ยังอยู่ตรงนั้น"
            )
        ago = f"{hours:.0f}h ago" if hours >= 1 else "less than an hour ago"
        return (
            f"{self.label} was last seen at {where} {ago} "
            f"(confidence {self.confidence:.0%}). A sighting, not a guarantee."
        )


class SpatialMemory:
    def __init__(self, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        self._observations: list[Observation] = []
        self._retention = timedelta(days=retention_days)

    def record(
        self,
        label: str,
        *,
        confidence: float,
        location_context: str | None = None,
        device_id: UUID | None = None,
        camera_id: str | None = None,
        object_type: str = "object",
        bbox: dict[str, float] | None = None,
        seen_at: datetime | None = None,
    ) -> Observation:
        observation = Observation(
            label=label.strip().lower(),
            object_type=object_type,
            confidence=max(0.0, min(1.0, confidence)),
            location_context=location_context,
            device_id=device_id,
            camera_id=camera_id,
            bbox=bbox or {},
            seen_at=seen_at or datetime.now(UTC),
        )
        observation.expires_at = observation.seen_at + self._retention
        self._observations.append(observation)
        return observation

    def _matches(self, observation: Observation, label: str) -> bool:
        """Match on what the owner said *or* what the detector called it.

        Detector labels are English; the owner asks in Thai. Comparing only the raw string
        would mean "หนังสืออยู่ไหน" never finds a sighting labelled "book" — every spatial
        question from the primary user of this system, failing silently.
        """
        from thursday_vision.providers import canonical_label

        wanted = canonical_label(label)
        raw = label.strip().lower()
        return (
            wanted in observation.label
            or raw in observation.label
            or wanted == observation.object_type
        )

    def last_seen(self, label: str, *, min_confidence: float = 0.5) -> Observation | None:
        self.prune()
        matches = [
            o
            for o in self._observations
            if self._matches(o, label) and o.confidence >= min_confidence
        ]
        return max(matches, key=lambda o: o.seen_at, default=None)

    def history(self, label: str, *, limit: int = 10) -> list[Observation]:
        self.prune()
        matches = [o for o in self._observations if self._matches(o, label)]
        return sorted(matches, key=lambda o: o.seen_at, reverse=True)[:limit]

    def prune(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        before = len(self._observations)
        self._observations = [
            o for o in self._observations if not o.expires_at or o.expires_at > now
        ]
        return before - len(self._observations)

    def forget_all(self) -> int:
        """Part of the privacy controls: the owner can wipe what was seen (§68)."""
        count = len(self._observations)
        self._observations.clear()
        log.info("spatial_memory_cleared", removed=count)
        return count

    def objects(self, *, min_confidence: float = 0.5) -> list[TrackedObject]:
        """The per-object view: what has been seen, when it was first and last seen.

        Assembled from the sightings rather than stored separately. A sighting is a fact
        that happened; an object's "current location" is an inference, and keeping only the
        inference would lose the evidence it rests on — including the fact that the last
        sighting was three days ago, which is usually the most important part of the answer.
        """
        self.prune()
        grouped: dict[str, list[Observation]] = {}
        for observation in self._observations:
            if observation.confidence >= min_confidence:
                grouped.setdefault(observation.label, []).append(observation)

        tracked: list[TrackedObject] = []
        for label, sightings in grouped.items():
            ordered = sorted(sightings, key=lambda o: o.seen_at)
            latest = ordered[-1]
            tracked.append(
                TrackedObject(
                    label=label,
                    object_type=latest.object_type,
                    camera_id=latest.camera_id,
                    location_context=latest.location_context,
                    position=latest.position,
                    confidence=latest.confidence,
                    first_seen=ordered[0].seen_at,
                    last_seen=latest.seen_at,
                    sightings=len(ordered),
                )
            )
        return sorted(tracked, key=lambda t: t.last_seen, reverse=True)

    def of_type(self, object_type: str) -> list[TrackedObject]:
        return [t for t in self.objects() if t.object_type == object_type.strip().lower()]

    def __len__(self) -> int:
        return len(self._observations)
