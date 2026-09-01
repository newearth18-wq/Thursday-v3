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
    confidence: float = 0.0
    location_context: str | None = None
    device_id: UUID | None = None
    bbox: dict[str, float] = field(default_factory=dict)
    seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

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
        bbox: dict[str, float] | None = None,
        seen_at: datetime | None = None,
    ) -> Observation:
        observation = Observation(
            label=label.strip().lower(),
            confidence=max(0.0, min(1.0, confidence)),
            location_context=location_context,
            device_id=device_id,
            bbox=bbox or {},
            seen_at=seen_at or datetime.now(UTC),
        )
        observation.expires_at = observation.seen_at + self._retention
        self._observations.append(observation)
        return observation

    def last_seen(self, label: str, *, min_confidence: float = 0.5) -> Observation | None:
        self.prune()
        matches = [
            o
            for o in self._observations
            if label.strip().lower() in o.label and o.confidence >= min_confidence
        ]
        return max(matches, key=lambda o: o.seen_at, default=None)

    def history(self, label: str, *, limit: int = 10) -> list[Observation]:
        self.prune()
        matches = [o for o in self._observations if label.strip().lower() in o.label]
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

    def __len__(self) -> int:
        return len(self._observations)
