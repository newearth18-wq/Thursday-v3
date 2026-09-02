"""Device Router (§22).

Resolves "this machine", "the laptop", "the PC at home", "the one from before" to an actual
node. Ambiguity produces a question, never a guess — a device action on the wrong machine
is one of the least forgivable mistakes an assistant can make.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from uuid import UUID

from thursday_shared.enums import DeviceStatus
from thursday_shared.models import DeviceSummary, WorldStateSnapshot

from thursday_core.focus import Focus

#: Below this, Thursday asks instead of picking.
CONFIDENCE_FLOOR = 0.7

_THIS_WORDS = {"this", "here", "เครื่องนี้", "ที่นี่", "นี่", "current"}
_LAST_WORDS = {"เมื่อกี้", "ก่อนหน้า", "previous", "last", "ที่แล้ว", "ตะกี้"}
_KIND_WORDS: dict[str, str] = {
    "laptop": "laptop",
    "โน้ตบุ๊ก": "laptop",
    "โน๊ตบุ๊ค": "laptop",
    "notebook": "laptop",
    "phone": "phone",
    "มือถือ": "phone",
    "โทรศัพท์": "phone",
    "มือ ถือ": "phone",
    "server": "server",
    "เซิร์ฟเวอร์": "server",
    "desktop": "desktop",
    "pc": "desktop",
    "คอม": "desktop",
    "คอมพิวเตอร์": "desktop",
}


@dataclass(frozen=True)
class DeviceResolution:
    device: DeviceSummary | None
    confidence: float
    reason: str
    candidates: tuple[DeviceSummary, ...] = ()
    #: True when neither this sentence nor the owner's own machine chose the device —
    #: the conversation did. The reply must then say which machine it acted on, because
    #: the owner has no other way to find out where their command landed.
    announce: bool = False

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
        focus: Focus | None = None,
    ) -> DeviceResolution:
        """Work out which machine an instruction is for.

        ``focus`` is the device the conversation is already about (see `thursday_core.focus`).
        It applies only when the sentence names no device *and* does not say "this machine" —
        an explicit word always beats an inherited one, in both directions.
        """
        devices = [d for d in self._hub.online() if self._capable(d, required_capability)]  # type: ignore[attr-defined]
        if not devices:
            return DeviceResolution(None, 0.0, "no online device has the required capability")

        normalised = (hint or "").strip().lower()

        # An explicit "this machine" overrides the focus: the owner just moved the subject
        # back to where they are standing, and saying so must be enough.
        if normalised in _THIS_WORDS:
            focus = None

        if not normalised or normalised in _THIS_WORDS:
            candidates: list[tuple[UUID | None, str, float, bool]] = []
            if focus is not None:
                candidates.append(
                    (focus.device_id, f"the {focus.device_name} you just asked about", 0.85, True)
                )
            candidates += [
                (
                    origin_device_id,
                    "the device you're speaking from",
                    0.95 if hint else 0.85,
                    False,
                ),
                (world.active_device_id, "the active device", 0.95 if hint else 0.85, False),
            ]
            for candidate_id, reason, confidence, announce in candidates:
                if candidate_id is None:
                    continue
                match = next((d for d in devices if d.id == candidate_id), None)
                if match is not None:
                    return DeviceResolution(
                        match, confidence, reason, tuple(devices), announce=announce
                    )
            if len(devices) == 1:
                return DeviceResolution(devices[0], 0.8, "the only device online", tuple(devices))
            return DeviceResolution(
                None, 0.4, "no active device to anchor 'this' to", tuple(devices)
            )

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
                return DeviceResolution(
                    by_location[0], 0.82, f"the only device at {location}", tuple(devices)
                )
            if len(by_location) > 1:
                return DeviceResolution(
                    None, 0.5, f"several devices are at {location}", tuple(by_location)
                )

        scored = sorted(
            ((d, SequenceMatcher(None, normalised, d.name.lower()).ratio()) for d in devices),
            key=lambda pair: pair[1],
            reverse=True,
        )
        best, score = scored[0]
        if score >= 0.72 and (len(scored) == 1 or score - scored[1][1] > 0.15):
            return DeviceResolution(best, min(0.85, score), "closest name match", tuple(devices))
        return DeviceResolution(
            None, score, f"{hint!r} does not clearly match one device", tuple(devices)
        )

    def follow_me(
        self,
        *,
        world: WorldStateSnapshot,
        origin_device_id: UUID | None = None,
        capability: str = "audio",
    ) -> DeviceSummary | None:
        """Where the answer should come out (§9 follow-me, V8).

        A different question from `resolve`, which asks where the *work* happens. The owner
        can perfectly well ask their phone to do something on the office PC and still want
        the answer on the phone — those are not the same device and conflating them means
        Thursday replies to an empty room.

        Presence is inferred from the last thing the owner actually did, because that is the
        only evidence there is. Thursday does not know where anybody is; it knows which
        machine last had a person typing or talking at it, and treats a more recent
        interaction as better evidence than an older one. That is a heuristic and it is
        wrong sometimes — which is why it only ever chooses where to *speak*, never what to
        do, and why the fallbacks below end at "say nothing out loud" rather than at a
        guess.
        """
        candidates = [
            d
            for d in self._hub.online()  # type: ignore[attr-defined]
            if d.capabilities.supports(capability)
        ]
        if not candidates:
            return None

        for device_id in (origin_device_id, world.active_device_id):
            if device_id is None:
                continue
            match = next((d for d in candidates if d.id == device_id), None)
            if match is not None:
                return match

        # Nothing anchors the owner to a machine. The most recently seen device is the best
        # remaining evidence, and one candidate is not evidence at all — it is the only
        # option, which is a different thing and worth not confusing.
        return max(candidates, key=lambda d: d.last_seen_at or datetime.min.replace(tzinfo=UTC))

    def _capable(self, device: DeviceSummary, capability: str | None) -> bool:
        if device.status is not DeviceStatus.ONLINE:
            return False
        return capability is None or device.capabilities.supports(capability)

    def _location_hint(self, text: str) -> str | None:
        for word, location in (
            ("home", "home"),
            ("ที่บ้าน", "home"),
            ("บ้าน", "home"),
            ("office", "office"),
            ("ที่ทำงาน", "office"),
            ("ออฟฟิศ", "office"),
            ("school", "school"),
            ("โรงเรียน", "school"),
        ):
            if word in text:
                return location
        return None
