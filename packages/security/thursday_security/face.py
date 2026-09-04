"""Faces: enrolling one, and refusing to believe a picture of one (§7, §12, §13, §14, §84).

§12 is the sentence this module is built around:

    "ห้ามถือว่าการเห็นใบหน้าในภาพหนึ่งเฟรมคือเจ้าของจริง"

A photograph of the owner matches a face template *perfectly*. It is the best possible match —
better than the owner on a bad day, at an angle, in poor light. So matching harder makes the
attack easier, not harder, and the only thing that separates the owner from a printout is
evidence that cannot be printed: movement over time, a response to something asked just now,
depth. That is why `liveness` is a separate axis from `confidence` throughout this module and
never blended into one score. Blending them ranks a good photograph above a real face.

**With no recogniser, Thursday refuses rather than pretends.** `NoFaceRecognition` is the
default, and it authenticates nobody. This follows `NoKeychain`, which does the same thing for
the same reason, and it matters more here: a stub that returned 0.9 would be a security hole
shaped like a feature, and whoever deployed it would believe they had face recognition. A
deployment that wants face recognition installs a provider; one that has not, has not.

**Nothing here infers anything about a person (§55).** There is no age, no expression, no
mood, no attribute of any kind — only "does this match the enrolled template" and "is this
alive". The enrollment poses exist to make matching robust across angles, not to build a
richer picture of anybody.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger

from thursday_security.biometrics import BiometricError
from thursday_security.identity import Factor, IdentityClaim

log = get_logger(__name__)


class Pose(StrEnum):
    """§7's enrollment coverage. Angles, not attributes.

    Several poses because a template built from one straight-on photograph fails the first
    time the owner tilts their head — and a system that fails on the owner teaches them to
    turn it off, which is the security outcome nobody counts.
    """

    FRONT = "FRONT"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    DOWN = "DOWN"
    UP = "UP"


#: Enrollment is not finished until every pose is covered. §7 lists them, and the failure of a
#: partial enrolment is not "slightly worse matching" — it is the owner being refused in the
#: one lighting condition they use most.
REQUIRED_POSES: frozenset[Pose] = frozenset(Pose)

#: How many usable frames per pose before it counts. One frame per angle is one blink or one
#: motion blur away from a template built on a bad sample.
FRAMES_PER_POSE = 3

#: §7 also asks for several lighting conditions. Tracked as distinct sample sets rather than
#: measured: Thursday cannot verify a room's lighting, but it can ask for enrolment in more
#: than one sitting and notice whether that happened.
MIN_LIGHTING_CONDITIONS = 2


class LivenessSignal(StrEnum):
    """§12's list. Each is a different thing a recording fails to do.

    They are kept separate rather than summed into one number because they defeat *different*
    attacks: a printed photo fails BLINK, a video loop passes BLINK and fails CHALLENGE, and a
    deepfake responding in real time may pass both and fail DEPTH.
    """

    BLINK = "BLINK"
    #: Movement between frames that is consistent with a head, not a hand holding a phone.
    HEAD_MOVEMENT = "HEAD_MOVEMENT"
    #: The frames actually differ over time. A still image repeated is not a video.
    TEMPORAL_VARIATION = "TEMPORAL_VARIATION"
    #: The face did the specific thing it was asked to do, just now (§14).
    CHALLENGE_RESPONSE = "CHALLENGE_RESPONSE"
    #: A depth camera says the face has shape. Hardware-dependent (§73).
    DEPTH = "DEPTH"
    #: An infrared camera says the face emits heat. Hardware-dependent.
    INFRARED = "INFRARED"


#: Signals a flat screen can produce, and therefore cannot prove anything on their own. A
#: phone playing a video of the owner blinks, moves and varies over time — §14's whole point.
#: Declared so that adding a signal forces somebody to decide which side it falls on.
REPLAYABLE: frozenset[LivenessSignal] = frozenset(
    {
        LivenessSignal.BLINK,
        LivenessSignal.HEAD_MOVEMENT,
        LivenessSignal.TEMPORAL_VARIATION,
    }
)

#: Signals a replay cannot fake: one requires responding to something chosen *after* the
#: recording would have been made, the others require physics a screen does not have.
UNREPLAYABLE: frozenset[LivenessSignal] = frozenset(
    {
        LivenessSignal.CHALLENGE_RESPONSE,
        LivenessSignal.DEPTH,
        LivenessSignal.INFRARED,
    }
)

#: Below this, the face is not treated as alive at all.
LIVE_ENOUGH = 0.6

#: The minimum number of frames any liveness assessment will consider. §12 in a constant:
#: one frame is a photograph, and no amount of confidence changes that.
MIN_FRAMES = 3


@dataclass(frozen=True)
class LivenessEvidence:
    """What was actually observed. Signals present, and how many frames they came from."""

    signals: frozenset[LivenessSignal] = frozenset()
    frames: int = 0
    #: Set when a challenge was issued and the response matched (§14).
    challenge_passed: bool = False

    def score(self) -> tuple[float, tuple[str, ...]]:
        """How alive this looks, and what argues against it.

        Deliberately not a weighted sum. The rule is about *kinds* of evidence: replayable
        signals raise a ceiling, unreplayable ones break through it. A video loop can produce
        every replayable signal at full strength and must still not reach the level a single
        challenge-response reaches.
        """
        concerns: list[str] = []

        if self.frames < MIN_FRAMES:
            # §12, stated as a floor rather than a heuristic. One frame is a photograph.
            return 0.0, ("a single image is not evidence that anybody is there",)

        replayable = self.signals & REPLAYABLE
        unreplayable = self.signals & UNREPLAYABLE

        if not replayable and not unreplayable:
            return 0.0, ("nothing about this looked like a living face",)

        if not unreplayable:
            # §14. Everything observed could have come off a screen, so the score is capped
            # below `LIVE_ENOUGH` however many replayable signals there are. This is the
            # branch that stops "it blinked and moved" being enough.
            concerns.append("everything observed could have been played from a screen")
            return min(0.5, 0.2 * len(replayable)), tuple(concerns)

        score = 0.6 + 0.15 * len(unreplayable) + 0.05 * len(replayable)
        if not replayable:
            concerns.append("no movement observed")
        return min(score, 1.0), tuple(concerns)

    @property
    def live(self) -> bool:
        return self.score()[0] >= LIVE_ENOUGH


@dataclass
class FaceEnrollment:
    """§7's multi-pose enrolment, and the deletion that follows it.

    Holds frames only while building a template. `finish()` produces the template and clears
    them, and there is no accessor that returns a frame — §7 says the raw images go, and the
    way to make that true is to give nobody a way to keep them.
    """

    user_id: str
    _samples: dict[Pose, list[Any]] = field(default_factory=dict)
    _sittings: int = 0

    def add(self, pose: Pose, frame: Any) -> None:
        self._samples.setdefault(pose, []).append(frame)

    def begin_sitting(self) -> None:
        """§7's several lighting conditions, as a thing the owner does rather than a thing
        Thursday measures: a second sitting, later, in a different room."""
        self._sittings += 1

    def covered(self) -> set[Pose]:
        return {p for p, frames in self._samples.items() if len(frames) >= FRAMES_PER_POSE}

    def missing(self) -> set[Pose]:
        return set(REQUIRED_POSES) - self.covered()

    @property
    def complete(self) -> bool:
        return not self.missing() and self._sittings >= MIN_LIGHTING_CONDITIONS

    def finish(self, provider: Any) -> bytes:
        """Build the template, then forget the frames.

        The order matters and so does the `finally`: an enrolment that raises partway through
        must not leave a directory of the owner's face behind.
        """
        if not self.complete:
            raise BiometricError(
                f"enrolment is not finished: still need {sorted(p.value for p in self.missing())}"
            )
        frames = [frame for pose in Pose for frame in self._samples.get(pose, [])]
        try:
            template = provider.extract_template(frames)
        finally:
            # §7: raw images are deleted once the template exists — and also when it does not.
            self._samples.clear()
        if not template:
            raise BiometricError("the face provider produced no template")
        log.info("face_enrolled", user=self.user_id, poses=len(REQUIRED_POSES))
        return template


class NoFaceRecognition:
    """What a machine with no face recogniser gets. Authenticates nobody.

    The same posture as `NoKeychain`, and for a sharper reason: a stub that returned a
    plausible confidence would be a security hole shaped like a feature. Whoever deployed it
    would believe they had face recognition, and the first time anyone tested it with a
    photograph they would find out they had something worse than nothing — a lock that opens
    for everybody while reporting that it is locked.

    So every method here refuses, and `available` says so out loud, which is what the
    capability detection in §72 reads.
    """

    name = "none"
    available = False

    def detect_face(self, frame: Any) -> bool:
        return False

    def extract_template(self, frames: list[Any]) -> bytes:
        raise BiometricError(
            "no face recogniser is installed on this machine, so no face can be enrolled"
        )

    def match_identity(self, frame: Any, template: bytes) -> float:
        # Zero, not an exception: matching is called on the authentication path, and a path
        # that raises is a path somebody wraps in a try/except that swallows it. Returning
        # "no match" is the same answer and cannot be caught into a success.
        return 0.0

    def perform_liveness_check(self, frames: list[Any]) -> float:
        return 0.0


class FaceMatcher:
    """Turns frames into a claim, with liveness kept separate from confidence throughout.

    Produces an `IdentityClaim`, which is one provider's opinion and never authoritative on
    its own — the fusion engine (§18) decides what it is worth alongside everything else.
    """

    def __init__(self, provider: Any, store: Any) -> None:
        self._provider = provider
        self._store = store

    def identify(
        self, *, user_id: str, frames: list[Any], evidence: LivenessEvidence
    ) -> IdentityClaim:
        """Is this the enrolled person, and is anybody actually there?

        Both questions, always, and reported separately. A caller that only looks at
        `confidence` is looking at how good the photograph was.
        """
        template = self._store.load_template(user_id=user_id, kind="face")
        if template is None:
            return IdentityClaim(
                factor=Factor.FACE, user_id=None, concerns=("no face is enrolled",)
            )

        if len(frames) < MIN_FRAMES:
            # Refused before matching. Matching a single frame and then discounting it later
            # is a code path that eventually gets its liveness check skipped by an
            # optimisation; not matching at all cannot.
            return IdentityClaim(
                factor=Factor.FACE,
                user_id=None,
                concerns=("a single image is not evidence that anybody is there",),
            )

        liveness, concerns = evidence.score()
        confidence = max(
            (self._provider.match_identity(frame, template) for frame in frames), default=0.0
        )

        if liveness < LIVE_ENOUGH:
            # §13: when unsure, do not accept — and do not silently accept at a lower level
            # either. The claim carries the reason so the fusion engine can decide whether
            # another factor closes the gap (§18's `required_next_factor`).
            log.info("face_liveness_insufficient", user=user_id)
            return IdentityClaim(
                factor=Factor.FACE,
                user_id=None,
                confidence=0.0,
                liveness=liveness,
                concerns=concerns or ("this did not look like a living face",),
            )

        return IdentityClaim(
            factor=Factor.FACE,
            user_id=user_id if confidence > 0 else None,
            confidence=confidence,
            liveness=liveness,
            concerns=concerns,
        )


def challenge_for(nonce: str) -> str:
    """§14's random instruction. Something to do *now*, which a recording cannot have done.

    The phrasing is deliberately physical and simple. A challenge the owner cannot perform
    reliably is one that trains them to retry until it passes, which is the same as not
    having one.
    """
    instructions = (
        "หันหน้าไปทางซ้ายช้า ๆ ครับ",
        "หันหน้าไปทางขวาช้า ๆ ครับ",
        "ก้มหน้าลงเล็กน้อยครับ",
        "เงยหน้าขึ้นเล็กน้อยครับ",
    )
    return instructions[hash(nonce) % len(instructions)]
