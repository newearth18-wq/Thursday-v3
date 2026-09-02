"""Hand tracking and gesture recognition (§27–29, V7).

The interpretation layer is real and testable from landmarks; the landmark *source*
(MediaPipe Hands) is a port, because the classification is the part with the judgement in
it and the part worth testing.

Two things run through everything here.

**A hand shape is not an instruction.** It is evidence of one, and evidence that is
misread constantly — by bad light, by a hand at an angle, by someone gesturing while
talking to a person in the room. Every gesture therefore carries a confidence, commands are
only read while gesture mode is open, and `safety.py` refuses to let a gesture confirm
anything consequential on its own.

**Movement is half the vocabulary.** Swipe, drag and zoom cannot be seen in a single frame:
they are a hand *over time*. A classifier that only ever sees one frame cannot express them,
which is why `GestureTracker` holds a short history rather than `classify` being a pure
function of the current landmarks.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from thursday_core.logging import get_logger

log = get_logger(__name__)

GESTURE_MODE_TIMEOUT_S = 10.0
ACTIVATION_GESTURE = "peace"

#: How much of the frame a hand must cross to count as a swipe rather than a wobble.
SWIPE_MIN_TRAVEL = 0.18
#: And how quickly. A hand drifting across the frame over five seconds is not a swipe.
SWIPE_MAX_SECONDS = 0.9
#: Thumb tip to index tip, normalised. Below this the fingers are touching.
PINCH_MAX_DISTANCE = 0.06
#: A pinch that moves this far is a drag.
DRAG_MIN_TRAVEL = 0.08
#: How much the distance between two hands must change to read as a zoom.
ZOOM_MIN_CHANGE = 0.1
#: How far past the fingertip to project the aim. Small on purpose — see `aim_at`.
AIM_PROJECTION = 0.12


class Gesture(StrEnum):
    POINT = "point"
    PINCH = "pinch"
    DRAG = "drag"
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"
    OPEN_PALM = "open_palm"
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    PEACE = "peace"
    FIST = "fist"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    NONE = "none"


#: What each gesture means to the rest of the system. Kept here rather than at each call
#: site so "what does a swipe do" has one answer.
GESTURE_COMMANDS: dict[Gesture, str] = {
    Gesture.POINT: "pointer",
    Gesture.PINCH: "select",
    Gesture.DRAG: "drag",
    Gesture.SWIPE_LEFT: "previous",
    Gesture.SWIPE_RIGHT: "next",
    Gesture.OPEN_PALM: "stop",
    Gesture.THUMBS_UP: "confirm",
    Gesture.THUMBS_DOWN: "cancel",
    Gesture.ZOOM_IN: "zoom_in",
    Gesture.ZOOM_OUT: "zoom_out",
}


@dataclass
class HandLandmarks:
    """21 normalised (x, y) points, in MediaPipe's ordering."""

    points: list[tuple[float, float]] = field(default_factory=list)
    handedness: str = "right"
    #: The tracker's own confidence that this is a hand at all. Distinct from the
    #: confidence that a gesture was recognised, and both matter.
    confidence: float = 1.0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    #: Fingertip and PIP-joint indices, for the extension test.
    TIPS = (4, 8, 12, 16, 20)
    PIPS = (3, 6, 10, 14, 18)
    WRIST = 0
    THUMB_TIP = 4
    INDEX_TIP = 8
    INDEX_PIP = 6

    @property
    def valid(self) -> bool:
        return len(self.points) >= 21

    def extended(self) -> list[bool]:
        """A finger is extended when its tip sits above its middle joint."""
        if not self.valid:
            return [False] * 5
        out = []
        for tip, pip in zip(self.TIPS, self.PIPS, strict=True):
            if tip == self.THUMB_TIP:  # the thumb opposes sideways, not vertically
                out.append(abs(self.points[tip][0] - self.points[pip][0]) > 0.04)
            else:
                out.append(self.points[tip][1] < self.points[pip][1])
        return out

    def index_tip(self) -> tuple[float, float] | None:
        return self.points[self.INDEX_TIP] if len(self.points) > self.INDEX_TIP else None

    def centre(self) -> tuple[float, float] | None:
        """The wrist. A stable anchor for measuring how the whole hand moved."""
        return self.points[self.WRIST] if self.valid else None

    def pinch_distance(self) -> float:
        """Thumb tip to index tip.

        This is what distinguishes a pinch from a fist, and getting it wrong means every
        closed hand reads as a select — which was the behaviour before V7.
        """
        if not self.valid:
            return 1.0
        (tx, ty), (ix, iy) = self.points[self.THUMB_TIP], self.points[self.INDEX_TIP]
        return math.hypot(tx - ix, ty - iy)

    def pointing_direction(self) -> tuple[float, float] | None:
        """A unit vector along the index finger, from joint to tip.

        Where the finger *aims*, which is not the same as where its tip happens to be —
        and aim is what resolves "that one over there" on a screen the hand is not touching.
        """
        if not self.valid:
            return None
        (px, py), (tx, ty) = self.points[self.INDEX_PIP], self.points[self.INDEX_TIP]
        dx, dy = tx - px, ty - py
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return None
        return dx / length, dy / length

    def aim_at(self, distance: float = AIM_PROJECTION) -> tuple[float, float] | None:
        """Where on the screen the finger is indicating.

        A short projection past the fingertip, not a ray cast. The fingertip alone lags
        slightly behind where someone means — the finger points *ahead* of itself — and
        this corrects for that. Projecting further would be pretending to know the hand's
        distance and angle to the screen, which two-dimensional landmarks cannot tell us:
        a long projection sends the aim off the edge of the frame as often as it improves
        it. Real ray casting needs depth, and depth is not in this version.
        """
        tip = self.index_tip()
        direction = self.pointing_direction()
        if tip is None or direction is None:
            return None
        x = min(1.0, max(0.0, tip[0] + direction[0] * distance))
        y = min(1.0, max(0.0, tip[1] + direction[1] * distance))
        return x, y


@dataclass
class GestureReading:
    """One recognised gesture, with everything the caller needs to act or refuse."""

    gesture: Gesture = Gesture.NONE
    confidence: float = 0.0
    hands: int = 0
    #: Where the finger points, projected onto the screen. None unless pointing.
    pointing_at: tuple[float, float] | None = None
    #: How far the hand travelled, for drags and swipes.
    travel: float = 0.0
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def command(self) -> str | None:
        return GESTURE_COMMANDS.get(self.gesture)

    def __bool__(self) -> bool:
        return self.gesture is not Gesture.NONE


def classify(hands: list[HandLandmarks]) -> tuple[Gesture, float]:
    """One frame, one static gesture.

    Static only: swipe, drag and zoom need history and are the tracker's job. Kept as a
    separate function because a shape you can name from a single photograph is a different
    kind of claim from one that depends on what happened a moment ago.
    """
    if not hands:
        return Gesture.NONE, 0.0

    hand = hands[0]
    if not hand.valid:
        return Gesture.NONE, 0.0

    fingers = hand.extended()
    thumb, index, middle, ring, pinky = fingers
    count = sum(fingers)

    # Pinch before fist: both have few fingers extended, and the distance between the
    # thumb and index tips is the only thing that tells them apart.
    pinch = hand.pinch_distance()
    if pinch <= PINCH_MAX_DISTANCE and not (middle and ring and pinky):
        # Closer fingers, higher confidence — a firm pinch is unambiguous, a loose one is
        # a hand that might just be relaxed.
        return Gesture.PINCH, min(0.95, 0.6 + (PINCH_MAX_DISTANCE - pinch) * 5)

    if count == 5:
        return Gesture.OPEN_PALM, 0.9
    if index and middle and not ring and not pinky:
        return Gesture.PEACE, 0.9
    if index and count == 1:
        return Gesture.POINT, 0.9
    if thumb and count == 1:
        wrist = hand.points[hand.WRIST]
        tip = hand.points[hand.THUMB_TIP]
        return (Gesture.THUMBS_UP if tip[1] < wrist[1] else Gesture.THUMBS_DOWN), 0.85
    if count == 0:
        # Nothing extended and the fingers are apart: a closed hand. Reading this as a
        # pinch — as this module did before V7 — makes every resting hand a click.
        return Gesture.FIST, 0.7
    return Gesture.NONE, 0.3


class GestureTracker:
    """Recognises gestures that only exist over time.

    Holds a short window of frames. Long enough to see a swipe, short enough that a hand
    which paused mid-motion does not trigger one a second later.
    """

    def __init__(self, *, window: int = 12) -> None:
        self._frames: deque[tuple[datetime, list[HandLandmarks]]] = deque(maxlen=window)

    def reset(self) -> None:
        self._frames.clear()

    def observe(self, hands: list[HandLandmarks], *, now: datetime | None = None) -> GestureReading:
        now = now or datetime.now(UTC)
        self._frames.append((now, hands))

        if not hands:
            return GestureReading(at=now)

        if len(hands) >= 2:
            return self._two_hand(hands, now)

        static, confidence = classify(hands)
        hand = hands[0]

        # A moving pinch is a drag; a moving open hand is a swipe. Both need history.
        travel, dx, elapsed = self._movement()

        if static is Gesture.PINCH and travel >= DRAG_MIN_TRAVEL:
            return GestureReading(
                gesture=Gesture.DRAG, confidence=confidence, hands=1, travel=travel, at=now
            )

        if (
            static in (Gesture.OPEN_PALM, Gesture.POINT)
            and travel >= SWIPE_MIN_TRAVEL
            and elapsed <= SWIPE_MAX_SECONDS
            and abs(dx) >= SWIPE_MIN_TRAVEL * 0.8  # mostly sideways, not a wave upward
        ):
            gesture = Gesture.SWIPE_RIGHT if dx > 0 else Gesture.SWIPE_LEFT
            return GestureReading(gesture=gesture, confidence=0.8, hands=1, travel=travel, at=now)

        return GestureReading(
            gesture=static,
            confidence=confidence * hand.confidence,
            hands=1,
            pointing_at=hand.aim_at() if static is Gesture.POINT else None,
            at=now,
        )

    def _movement(self) -> tuple[float, float, float]:
        """How far the wrist travelled across the window, and over how long."""
        positions = [
            (at, frame[0].centre())
            for at, frame in self._frames
            if len(frame) == 1 and frame[0].centre() is not None
        ]
        if len(positions) < 2:
            return 0.0, 0.0, 0.0
        (first_at, start), (last_at, end) = positions[0], positions[-1]
        dx, dy = end[0] - start[0], end[1] - start[1]
        return math.hypot(dx, dy), dx, (last_at - first_at).total_seconds()

    def _two_hand(self, hands: list[HandLandmarks], now: datetime) -> GestureReading:
        """Expand or contract. Direction is the whole meaning — "two hands moved" is not
        an instruction, "the hands moved apart" is."""
        spread = self._spread(hands)
        earlier = None
        for _, frame in self._frames:
            if len(frame) >= 2:
                earlier = self._spread(frame)
                break
        if earlier is None or spread is None:
            return GestureReading(gesture=Gesture.NONE, confidence=0.3, hands=len(hands), at=now)

        change = spread - earlier
        if abs(change) < ZOOM_MIN_CHANGE:
            return GestureReading(gesture=Gesture.NONE, confidence=0.3, hands=len(hands), at=now)
        gesture = Gesture.ZOOM_IN if change > 0 else Gesture.ZOOM_OUT
        return GestureReading(
            gesture=gesture,
            confidence=min(0.9, 0.5 + abs(change)),
            hands=len(hands),
            travel=abs(change),
            at=now,
        )

    @staticmethod
    def _spread(hands: list[HandLandmarks]) -> float | None:
        centres = [h.centre() for h in hands[:2]]
        if any(c is None for c in centres):
            return None
        (ax, ay), (bx, by) = centres  # type: ignore[misc]
        return math.hypot(ax - bx, ay - by)


class GestureState(StrEnum):
    #: Not watching. Hand movement is just hand movement.
    OFF = "OFF"
    #: Watching for the activation gesture, and for nothing else.
    ARMED = "ARMED"
    #: Commands are read.
    ACTIVE = "ACTIVE"
    #: A command just fired. Briefly deaf, so one gesture is one command.
    COOLDOWN = "COOLDOWN"


#: How long after a command before the next one is read. At thirty frames a second, a
#: half-second thumbs-up is fifteen frames — without this, that is fifteen confirmations.
COOLDOWN_SECONDS = 0.6


@dataclass
class GestureMode:
    """Commands are only interpreted while this is ACTIVE (§28).

    Four states rather than a boolean, because two of the transitions carry the safety:
    ARMED is what stops an ordinary wave being a command, and COOLDOWN is what stops one
    gesture being sixty.
    """

    state: GestureState = GestureState.OFF
    opened_at: datetime | None = None
    last_interaction: datetime | None = None
    cooldown_until: datetime | None = None
    timeout_s: float = GESTURE_MODE_TIMEOUT_S
    cooldown_s: float = COOLDOWN_SECONDS
    #: Every command that fired, for the audit trail and for tests.
    commands: list[tuple[datetime, Gesture]] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """Kept for callers that only care whether commands are being read."""
        return self.state in (GestureState.ACTIVE, GestureState.COOLDOWN)

    @property
    def watching(self) -> bool:
        """True when the camera is being used for gestures at all. Drives the indicator."""
        return self.state is not GestureState.OFF

    def arm(self, *, now: datetime | None = None) -> None:
        """Start watching for the activation gesture — and for nothing else."""
        self.state = GestureState.ARMED
        self.last_interaction = now or datetime.now(UTC)

    def open(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        self.state = GestureState.ACTIVE
        self.opened_at = now
        self.last_interaction = now
        log.debug("gesture_mode_opened")

    def close(self) -> None:
        self.state = GestureState.OFF
        self.opened_at = None
        self.last_interaction = None
        self.cooldown_until = None

    def touch(self, *, now: datetime | None = None) -> None:
        self.last_interaction = now or datetime.now(UTC)

    def expired(self, *, now: datetime | None = None) -> bool:
        if self.state is GestureState.OFF or self.last_interaction is None:
            return False
        return (now or datetime.now(UTC)) - self.last_interaction > timedelta(
            seconds=self.timeout_s
        )

    def cooling(self, *, now: datetime | None = None) -> bool:
        if self.cooldown_until is None:
            return False
        return (now or datetime.now(UTC)) < self.cooldown_until

    def observe(
        self,
        gesture: Gesture | GestureReading,
        *,
        wake_word: bool = False,
        now: datetime | None = None,
    ) -> Gesture | None:
        """Feed one recognised gesture in; get back the *command*, or None.

        Outside ACTIVE, only the activation gesture or a wake word has any effect — so an
        ordinary wave is never a command. Inside it, a fired command starts a cooldown, so
        holding a gesture is one instruction rather than one per frame.
        """
        now = now or datetime.now(UTC)
        reading = (
            gesture if isinstance(gesture, GestureReading) else GestureReading(gesture=gesture)
        )
        recognised = reading.gesture

        if self.expired(now=now):
            log.debug("gesture_mode_expired")
            self.close()

        if self.state in (GestureState.OFF, GestureState.ARMED):
            if wake_word or recognised.value == ACTIVATION_GESTURE:
                self.open(now=now)
            return None

        if self.cooling(now=now):
            # Still hearing the last command. Keep the mode alive — the owner is plainly
            # still there — but read nothing.
            self.touch(now=now)
            return None
        if self.state is GestureState.COOLDOWN:
            self.state = GestureState.ACTIVE

        self.touch(now=now)
        if recognised is Gesture.NONE:
            return None

        self.state = GestureState.COOLDOWN
        self.cooldown_until = now + timedelta(seconds=self.cooldown_s)
        self.commands.append((now, recognised))
        return recognised

    def snapshot(self) -> dict:
        return {
            "state": str(self.state),
            "watching": self.watching,
            "active": self.active,
            "cooling": self.cooling(),
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "commands": len(self.commands),
        }
