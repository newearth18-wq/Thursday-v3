"""Privacy classification (§34) and privacy zones (§68).

The classifier decides *where* computation may happen. SECRET never leaves the machine —
this is enforced in ``ModelRouter``, not left to a prompt instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from uuid import UUID

from thursday.security.redaction import SecretRedactor
from thursday.shared.enums import DataSensitivity

#: Signals that push a payload up a level. Deliberately conservative.
_HIGHLY_PRIVATE_MARKERS = re.compile(
    r"(?i)\b(medical|diagnosis|prescription|salary|เงินเดือน|บัตรประชาชน|national\s*id|"
    r"passport|หนังสือเดินทาง|bank\s*account|เลขบัญชี|credit\s*card|ssn|tax\s*id|"
    r"therapy|mental\s*health|โรคประจำตัว)\b"
)
_PRIVATE_MARKERS = re.compile(
    r"(?i)\b(personal|private|ส่วนตัว|ห้ามเผยแพร่|confidential|ลับ|internal\s*only|"
    r"home\s*address|ที่อยู่บ้าน|phone\s*number|เบอร์โทร)\b"
)
_PUBLIC_MARKERS = re.compile(
    r"(?i)\b(weather|อากาศ|news|ข่าว|definition|แปลว่า|wikipedia|public\s*holiday|วันหยุด)\b"
)


@dataclass(frozen=True)
class Classification:
    level: DataSensitivity
    reasons: tuple[str, ...]

    @property
    def cloud_allowed(self) -> bool:
        return self.level < DataSensitivity.SECRET

    @property
    def prefers_local(self) -> bool:
        return self.level >= DataSensitivity.HIGHLY_PRIVATE


class PrivacyClassifier:
    """Cheap, deterministic, and auditable. An LLM is never asked to classify its own input."""

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        self._redactor = redactor or SecretRedactor()

    def classify(self, text: str, *, hints: dict[str, object] | None = None) -> Classification:
        hints = hints or {}
        reasons: list[str] = []

        if hits := self._redactor.scan(text):
            return Classification(DataSensitivity.SECRET, (f"credential_pattern:{hits[0]}",))
        if hints.get("contains_credentials"):
            return Classification(DataSensitivity.SECRET, ("caller_declared_credentials",))

        level = DataSensitivity.INTERNAL

        if _HIGHLY_PRIVATE_MARKERS.search(text):
            level = DataSensitivity.HIGHLY_PRIVATE
            reasons.append("sensitive_personal_marker")
        elif _PRIVATE_MARKERS.search(text):
            level = DataSensitivity.PRIVATE
            reasons.append("private_marker")
        elif _PUBLIC_MARKERS.search(text) and len(text) < 240:
            level = DataSensitivity.PUBLIC
            reasons.append("public_topic")

        # Structural signals outrank lexical ones.
        if hints.get("has_screen_content"):
            level = max(level, DataSensitivity.PRIVATE)
            reasons.append("screen_capture_in_context")
        if hints.get("has_camera_frame"):
            level = max(level, DataSensitivity.HIGHLY_PRIVATE)
            reasons.append("camera_frame_in_context")
        if hints.get("file_paths"):
            level = max(level, DataSensitivity.PRIVATE)
            reasons.append("local_file_reference")

        return Classification(level, tuple(reasons) or ("default_internal",))


@dataclass
class PrivacyZone:
    """§68. A named set of prohibitions bound to device, location, time, or mode."""

    name: str
    device_ids: set[UUID] = field(default_factory=set)
    location_contexts: set[str] = field(default_factory=set)
    modes: set[str] = field(default_factory=set)
    start: time | None = None
    end: time | None = None
    camera_disabled: bool = False
    microphone_disabled: bool = False
    memory_disabled: bool = False
    cloud_disabled: bool = False

    def applies(
        self,
        *,
        device_id: UUID | None = None,
        location: str | None = None,
        mode: str | None = None,
        now: time | None = None,
    ) -> bool:
        if self.device_ids and device_id not in self.device_ids:
            return False
        if self.location_contexts and (location or "") not in self.location_contexts:
            return False
        if self.modes and (mode or "") not in self.modes:
            return False
        if self.start and self.end and now is not None:
            in_window = (
                self.start <= now <= self.end
                if self.start <= self.end
                else now >= self.start or now <= self.end  # window crosses midnight
            )
            if not in_window:
                return False
        return True


class PrivacyZoneRegistry:
    def __init__(self, zones: list[PrivacyZone] | None = None) -> None:
        self._zones = list(zones or [])

    def add(self, zone: PrivacyZone) -> None:
        self._zones.append(zone)

    def active(self, **ctx: object) -> list[PrivacyZone]:
        return [z for z in self._zones if z.applies(**ctx)]  # type: ignore[arg-type]

    def forbids(self, surface: str, **ctx: object) -> str | None:
        """Return the name of the zone forbidding ``surface``, or None."""
        attr = f"{surface}_disabled"
        for zone in self.active(**ctx):
            if getattr(zone, attr, False):
                return zone.name
        return None
