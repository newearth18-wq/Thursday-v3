"""Audio routing (V4).

Thursday is one assistant across several machines, so "say this out loud" is not a complete
instruction — it has to be said *somewhere*. The router picks that somewhere.

The rule it follows is deliberately simple and, more importantly, deliberately conservative:
**speak where the owner is, and when in doubt, speak where they last spoke to you.** An
assistant that answers a question asked at the desk through the speaker in the kitchen is
worse than one with no routing at all, because the owner has to go and find the answer.

Follow-me is opt-in for the same reason. Output moving on its own is a surprise, and a
surprise involving audio in a house with other people in it is the kind that ends with the
feature turned off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from thursday_core.logging import get_logger

from thursday_voice.ports import AudioDevice

log = get_logger(__name__)


@dataclass
class RoutingDecision:
    device: AudioDevice | None
    reason: str

    def __bool__(self) -> bool:
        return self.device is not None


@dataclass
class AudioRouter:
    """Knows every audio endpoint Thursday can reach, and which to use."""

    devices: dict[str, AudioDevice] = field(default_factory=dict)
    #: Where the owner last spoke from. The strongest signal there is.
    active_device_id: str | None = None
    #: An explicit choice by the owner, which outranks every heuristic below.
    preferred_output_id: str | None = None
    #: §46 — output follows presence. Off by default: audio moving on its own is a surprise.
    follow_me: bool = False

    def register(self, device: AudioDevice) -> AudioDevice:
        self.devices[device.id] = device
        log.debug("audio_device_registered", device=device.id, kind=device.kind)
        return device

    def remove(self, device_id: str) -> None:
        self.devices.pop(device_id, None)
        if self.preferred_output_id == device_id:
            self.preferred_output_id = None

    def set_available(self, device_id: str, available: bool) -> None:
        if (device := self.devices.get(device_id)) is not None:
            device.available = available

    def of_kind(self, kind: str) -> list[AudioDevice]:
        return [d for d in self.devices.values() if d.kind == kind and d.available]

    # ------------------------------------------------------------------ selection

    def microphone(self, *, device_id: str | None = None) -> RoutingDecision:
        """Where to listen. Almost always the machine being spoken to."""
        candidates = self.of_kind("microphone")
        if not candidates:
            return RoutingDecision(None, "no microphone is available")

        target = device_id or self.active_device_id
        if target:
            for mic in candidates:
                if mic.device_id == target or mic.id == target:
                    return RoutingDecision(mic, f"microphone on the active device {target}")

        for mic in candidates:
            if mic.is_default:
                return RoutingDecision(mic, "the default microphone")
        return RoutingDecision(candidates[0], "the only microphone available")

    def speaker(self, *, device_id: str | None = None, quiet: bool = False) -> RoutingDecision:
        """Where to speak.

        Order of precedence, strongest first:

        1. an explicit device for this reply;
        2. the owner's standing preference;
        3. a private endpoint when the reply is QUIET — a headset in the owner's ear beats
           a room speaker for something not everyone should hear (§43);
        4. the device they last spoke from;
        5. follow-me, if they turned it on;
        6. the default.
        """
        candidates = self.of_kind("speaker")
        if not candidates:
            return RoutingDecision(None, "no speaker is available")

        if device_id:
            for out in candidates:
                if out.id == device_id or out.device_id == device_id:
                    return RoutingDecision(out, "explicitly requested for this reply")

        if self.preferred_output_id:
            for out in candidates:
                if out.id == self.preferred_output_id:
                    return RoutingDecision(out, "the owner's preferred output")

        if quiet:
            private = [o for o in candidates if o.transport == "bluetooth" or "private" in o.tags]
            if private:
                return RoutingDecision(
                    private[0], "a private output, because this reply is not for the room"
                )

        if self.active_device_id:
            for out in candidates:
                if out.device_id == self.active_device_id:
                    return RoutingDecision(out, "the device the owner is using")

        if self.follow_me:
            present = [o for o in candidates if "present" in o.tags]
            if present:
                return RoutingDecision(present[0], "follow-me: where the owner is now")

        for out in candidates:
            if out.is_default:
                return RoutingDecision(out, "the default speaker")
        return RoutingDecision(candidates[0], "the only speaker available")

    def note_activity(self, device_id: str) -> None:
        """Record where the owner just spoke from."""
        self.active_device_id = device_id

    def prefer(self, device_id: str | None) -> None:
        """Set or clear the standing output preference."""
        if device_id is not None and device_id not in self.devices:
            raise KeyError(f"unknown audio device {device_id!r}")
        self.preferred_output_id = device_id

    def snapshot(self) -> dict:
        return {
            "active_device_id": self.active_device_id,
            "preferred_output_id": self.preferred_output_id,
            "follow_me": self.follow_me,
            "devices": [
                {
                    "id": d.id,
                    "name": d.name,
                    "kind": d.kind,
                    "transport": d.transport,
                    "device_id": d.device_id,
                    "available": d.available,
                    "default": d.is_default,
                }
                for d in self.devices.values()
            ],
        }
