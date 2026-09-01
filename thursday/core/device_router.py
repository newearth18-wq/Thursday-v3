"""Device Router (§22).

Resolves "this machine", "the laptop", "the PC at home", "the one from before" to an actual
node. Ambiguity produces a question, never a guess — a device action on the wrong machine
is one of the least forgivable mistakes an assistant can make.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID

from thursday.shared.enums import DeviceStatus
from thursday.shared.models import DeviceSummary, WorldStateSnapshot

#: Below this, Thursday asks instead of picking.
CONFIDENCE_FLOOR = 0.7

_THIS_WORDS = {"this", "here", "เครื่องนี้", "ที่นี่", "นี่", "current"}
_LAST_WORDS = {"เมื่อกี้", "ก่อนหน้า", "previous", "last", "ที่แล้ว", "ตะกี้"}
_KIND_WORDS: dict[str, str] = {
    "laptop": "laptop", "โน้ตบุ๊ก": "laptop", "โน๊ตบุ๊ค": "laptop", "notebook": "laptop",
    "phone": "phone", "มือถือ": "phone", "โทรศัพท์": "phone", "มือ ถือ": "phone",
    "server": "server", "เซิร์ฟเวอร์": "server",
    "desktop": "desktop", "pc": "desktop", "คอม": "desktop", "คอมพิวเตอร์": "desktop",
}


@dataclass(frozen=True)
class DeviceResolution:
    device: DeviceSummary | None
    confidence: float
    reason: str
    candidates: tuple[DeviceSummary, ...] = ()

    @property
    def needs_confirmation(self) -> bool:
        return self.device is None or self.confidence < CONFIDENCE_FLOOR

    def question(self) -> str:
        if not self.candidates:
            return "ไม่พบอุปกรณ์ที่ออนไลน์อยู่ ต้องการให้ผมทำบนเครื่องไหน"
        names = " / ".join(d.name for d in self.candidates[:4])
        return f"ต้องการให้ทำบนเครื่องไหน — {names}"


class DeviceRouter:
    def __init__(self, hub: object) -> None:
        self._hub = hub

    def resolve(
        self,
        hint: str | None,
        *,
        world: WorldStateSnapshot,
        origin_device_id: UUID | None = None,
        required_capability: str | None = None,
    ) -> DeviceResolution:
        devices = [d for d in self._hub.online() if self._capable(d, required_capability)]  # type: ignore[attr-defined]
        if not devices:
            return DeviceResolution(None, 0.0, "no online device has the required capability")

        normalised = (hint or "").strip().lower()

        # "this machine" — the device the turn came from, then the active device.
        if not normalised or normalised in _THIS_WORDS:
            for candidate_id, reason in (
                (origin_device_id, "the device you're speaking from"),
                (world.active_device_id, "the active device"),
            ):
                if candidate_id is None:
                    continue
                match = next((d for d in devices if d.id == candidate_id), None)
                if match is not None:
                    return DeviceResolution(match, 0.95 if hint else 0.85, reason, tuple(devices))
            if len(devices) == 1:
                return DeviceResolution(devices[0], 0.8, "the only device online", tuple(devices))
            return DeviceResolution(None, 0.4, "no active device to anchor 'this' to", tuple(devices))

        if normalised in _LAST_WORDS:
            match = next((d for d in devices if d.id == world.active_device_id), None)
            if match is not None:
                return DeviceResolution(match, 0.8, "the device used most recently", tuple(devices))

        # Exact, then prefix, then fuzzy name match.
        exact = [d for d in devices if d.name.lower() == normalised]
        if len(exact) == 1:
            return DeviceResolution(exact[0], 1.0, "exact name match", tuple(devices))

        prefix = [d for d in devices if d.name.lower().startswith(normalised)]
        if len(prefix) == 1:
            return DeviceResolution(prefix[0], 0.9, "unique name prefix", tuple(devices))
        if len(prefix) > 1:
            return DeviceResolution(None, 0.5, "several devices share that prefix", tuple(prefix))

        # Kind words ("the laptop") and location words ("at home").
        kind = next((k for word, k in _KIND_WORDS.items() if word in normalised), None)
        if kind:
            by_kind = [d for d in devices if d.kind == kind]
            location = self._location_hint(normalised)
            if location:
                # An explicit location narrows and never widens: "the PC at home" must not
                # quietly resolve to the office PC because no home PC is online.
                by_kind = [d for d in by_kind if (d.location_context or "").lower() == location]
                if not by_kind:
                    return DeviceResolution(
                        None, 0.3, f"no {kind} at {location} is online", tuple(devices)
                    )
            if len(by_kind) == 1:
                return DeviceResolution(by_kind[0], 0.85, f"the only {kind} online", tuple(devices))
            if len(by_kind) > 1:
                return DeviceResolution(None, 0.5, f"several {kind}s are online", tuple(by_kind))

        if location := self._location_hint(normalised):
            by_location = [d for d in devices if (d.location_context or "").lower() == location]
            if len(by_location) == 1:
                return DeviceResolution(by_location[0], 0.82, f"the only device at {location}", tuple(devices))
            if len(by_location) > 1:
                return DeviceResolution(None, 0.5, f"several devices are at {location}", tuple(by_location))

        scored = sorted(
            ((d, SequenceMatcher(None, normalised, d.name.lower()).ratio()) for d in devices),
            key=lambda pair: pair[1],
            reverse=True,
        )
        best, score = scored[0]
        if score >= 0.72 and (len(scored) == 1 or score - scored[1][1] > 0.15):
            return DeviceResolution(best, min(0.85, score), "closest name match", tuple(devices))
        return DeviceResolution(None, score, f"{hint!r} does not clearly match one device", tuple(devices))

    def _capable(self, device: DeviceSummary, capability: str | None) -> bool:
        if device.status is not DeviceStatus.ONLINE:
            return False
        return capability is None or device.capabilities.supports(capability)

    def _location_hint(self, text: str) -> str | None:
        for word, location in (
            ("home", "home"), ("ที่บ้าน", "home"), ("บ้าน", "home"),
            ("office", "office"), ("ที่ทำงาน", "office"), ("ออฟฟิศ", "office"),
            ("school", "school"), ("โรงเรียน", "school"),
        ):
            if word in text:
                return location
        return None
