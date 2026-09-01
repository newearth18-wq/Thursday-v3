"""Gesture recognition and gesture mode (§27–29).

The interpretation layer here is real and testable from landmarks; the landmark *source*
(MediaPipe Hands) arrives in Phase 3 behind ``HandTracker``.

Gesture mode matters as much as the recognition: interpreting every hand movement as a
command is how an assistant becomes unusable. Commands are read only while the mode is
open, and the mode closes itself after ten idle seconds (§28).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from thursday.core.logging import get_logger

log = get_logger(__name__)

GESTURE_MODE_TIMEOUT_S = 10.0
ACTIVATION_GESTURE = "peace"


class Gesture(StrEnum):
    POINT = "point"
    PINCH = "pinch"
    DRAG = "drag"
    SWIPE = "swipe"
    OPEN_PALM = "open_palm"
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    PEACE = "peace"
    TWO_HAND_ZOOM = "two_hand_zoom"
    NONE = "none"


@dataclass
class HandLandmarks:
    """21 normalised (x, y) points, in MediaPipe's ordering."""

    points: list[tuple[float, float]] = field(default_factory=list)
    handedness: str = "right"

    #: Fingertip and PIP-joint indices, for the extension test.
    TIPS = (4, 8, 12, 16, 20)
    PIPS = (3, 6, 10, 14, 18)

    def extended(self) -> list[bool]:
        """A finger is extended when its tip sits above its middle joint."""
        if len(self.points) < 21:
            return [False] * 5
        out = []
        for tip, pip in zip(self.TIPS, self.PIPS, strict=True):
            if tip == 4:  # the thumb opposes sideways, not vertically
                out.append(abs(self.points[tip][0] - self.points[pip][0]) > 0.04)
            else:
                out.append(self.points[tip][1] < self.points[pip][1])
        return out

    def index_tip(self) -> tuple[float, float] | None:
        return self.points[8] if len(self.points) > 8 else None


def classify(hands: list[HandLandmarks]) -> tuple[Gesture, float]:
    """Map landmarks to a gesture and a confidence."""
    if not hands:
        return Gesture.NONE, 0.0
    if len(hands) >= 2:
        return Gesture.TWO_HAND_ZOOM, 0.7

    fingers = hands[0].extended()
    thumb, index, middle, ring, pinky = fingers
    count = sum(fingers)

    if count == 5:
        return Gesture.OPEN_PALM, 0.9
    if index and middle and not ring and not pinky:
        return Gesture.PEACE, 0.9
    if index and count == 1:
        return Gesture.POINT, 0.9
    if thumb and count == 1:
        # Thumb direction decides up from down; without a wrist reference, stay unsure.
        points = hands[0].points
        if len(points) > 4:
            return (Gesture.THUMBS_UP if points[4][1] < points[0][1] else Gesture.THUMBS_DOWN), 0.8
        return Gesture.THUMBS_UP, 0.5
    if count == 0:
        return Gesture.PINCH, 0.6
    return Gesture.NONE, 0.3


@dataclass
class GestureMode:
    """Commands are only interpreted while this is open (§28)."""

    active: bool = False
    opened_at: datetime | None = None
    last_interaction: datetime | None = None
    timeout_s: float = GESTURE_MODE_TIMEOUT_S

    def open(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        self.active = True
        self.opened_at = now
        self.last_interaction = now
        log.debug("gesture_mode_opened")

    def close(self) -> None:
        self.active = False
        self.opened_at = None
        self.last_interaction = None

    def touch(self, *, now: datetime | None = None) -> None:
        self.last_interaction = now or datetime.now(UTC)

    def expired(self, *, now: datetime | None = None) -> bool:
        if not self.active or self.last_interaction is None:
            return False
        return (now or datetime.now(UTC)) - self.last_interaction > timedelta(
            seconds=self.timeout_s
        )

    def observe(
        self, gesture: Gesture, *, wake_word: bool = False, now: datetime | None = None
    ) -> Gesture | None:
        """Feed one recognised gesture in; get back the *command*, or None.

        Outside gesture mode only the activation gesture (or a wake word) has any effect —
        so an ordinary wave is never a command.
        """
        now = now or datetime.now(UTC)
        if self.expired(now=now):
            self.close()
            log.debug("gesture_mode_expired")

        if not self.active:
            if wake_word or gesture.value == ACTIVATION_GESTURE:
                self.open(now=now)
            return None

        self.touch(now=now)
        return None if gesture is Gesture.NONE else gesture
