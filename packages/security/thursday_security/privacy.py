"""Privacy classification (§34) and privacy zones (§68).

The classifier decides *where* computation may happen. SECRET never leaves the machine —
this is enforced in ``ModelRouter``, not left to a prompt instruction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from uuid import UUID

from thursday_shared.enums import DataSensitivity

from thursday_security.redaction import SecretRedactor

#: Signals that push a payload up a level. Deliberately conservative.
#
# Thai is written without spaces and ``\b`` does not delimit Thai script, so the markers
# are split: Latin terms use word boundaries, Thai terms match as substrings. A single
# combined pattern silently fails to match every Thai term in it.
_HIGHLY_PRIVATE_EN = re.compile(
    r"(?i)\b(medical|diagnosis|prescription|salary|payroll|national\s*id|passport|"
    r"bank\s*account|credit\s*card|ssn|tax\s*id|therapy|mental\s*health|"
    r"health\s*record)\b"
)
_HIGHLY_PRIVATE_TH = re.compile(
    r"(เงินเดือน|บัตรประชาชน|หนังสือเดินทาง|เลขบัญชี|บัญชีธนาคาร|บัตรเครดิต|"
    r"โรคประจำตัว|ผลตรวจสุขภาพ|ประวัติการรักษา|สุขภาพจิต|เลขประจำตัวผู้เสียภาษี)"
)
_PRIVATE_EN = re.compile(
    r"(?i)\b(personal|private|confidential|internal\s*only|home\s*address|"
    r"phone\s*number|do\s*not\s*share)\b"
)
_PRIVATE_TH = re.compile(r"(ส่วนตัว|ห้ามเผยแพร่|ความลับ|ลับเฉพาะ|ที่อยู่บ้าน|เบอร์โทร)")
_PUBLIC_EN = re.compile(r"(?i)\b(weather|news|definition|wikipedia|public\s*holiday|time\s*zone)\b")
_PUBLIC_TH = re.compile(r"(พยากรณ์อากาศ|สภาพอากาศ|ข่าววันนี้|แปลว่า|วันหยุดราชการ)")


def _matches(text: str, latin: re.Pattern[str], thai: re.Pattern[str]) -> bool:
    return bool(latin.search(text) or thai.search(text))


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

        if _matches(text, _HIGHLY_PRIVATE_EN, _HIGHLY_PRIVATE_TH):
            level = DataSensitivity.HIGHLY_PRIVATE
            reasons.append("sensitive_personal_marker")
        elif _matches(text, _PRIVATE_EN, _PRIVATE_TH):
            level = DataSensitivity.PRIVATE
            reasons.append("private_marker")
        elif _matches(text, _PUBLIC_EN, _PUBLIC_TH) and len(text) < 240:
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
