"""Faces, and refusing to believe a picture of one (BIOMETRIC IDENTITY §84) — Sprint 74.

§12's sentence is the whole sprint: *"ห้ามถือว่าการเห็นใบหน้าในภาพหนึ่งเฟรมคือเจ้าของจริง."*

A photograph of the owner matches a template perfectly — better than the owner at an angle in
bad light. So the tests that matter are not about matching. They are about the cases where
matching succeeds and the answer must still be no.

**What these tests cannot do.** This container has no camera and no face recogniser. Every
frame below is a Python object standing in for one, and `ScriptedProvider` returns numbers a
test chose. So this suite proves the *policy* — one frame is never enough, replayable evidence
never reaches the bar, uncertainty refuses rather than degrading quietly — and proves nothing
whatsoever about whether a real recogniser would tell two people apart. That part is untested
and will stay untested until this runs on a machine with a camera.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from thursday_security.biometrics import BiometricError, SecureBiometricStore
from thursday_security.face import (
    FRAMES_PER_POSE,
    LIVE_ENOUGH,
    MIN_FRAMES,
    MIN_LIGHTING_CONDITIONS,
    REPLAYABLE,
    REQUIRED_POSES,
    UNREPLAYABLE,
    FaceEnrollment,
    FaceMatcher,
    LivenessEvidence,
    LivenessSignal,
    NoFaceRecognition,
    Pose,
    challenge_for,
)


class ScriptedProvider:
    """A stand-in for a recogniser. Returns what a test told it to.

    Named for what it is. It proves the code around a provider behaves correctly and proves
    nothing about recognition — a distinction worth keeping visible in the type name, because
    a class called `LocalFaceProvider` would eventually be mistaken for one.
    """

    name = "scripted"
    available = True

    def __init__(self, *, match: float = 0.95, template: bytes = b"TEMPLATE") -> None:
        self._match = match
        self._template = template

    def detect_face(self, frame):
        return frame is not None

    def extract_template(self, frames):
        return self._template

    def match_identity(self, frame, template):
        return self._match if template == self._template else 0.0

    def perform_liveness_check(self, frames):
        return 1.0


@pytest.fixture
def store(tmp_path) -> SecureBiometricStore:
    return SecureBiometricStore(directory=tmp_path / "bio")


@pytest.fixture
def enrolled(store) -> SecureBiometricStore:
    store.store_template(user_id="owner", kind="face", template=b"TEMPLATE", provider="scripted")
    return store


def _live() -> LivenessEvidence:
    """Evidence that should pass: movement over time *and* something unreplayable."""
    return LivenessEvidence(
        signals=frozenset(
            {
                LivenessSignal.BLINK,
                LivenessSignal.HEAD_MOVEMENT,
                LivenessSignal.CHALLENGE_RESPONSE,
            }
        ),
        frames=10,
        challenge_passed=True,
    )


def _frames(n: int = 10) -> list[object]:
    return [object() for _ in range(n)]


# ============================================ §12 one frame is never evidence of anybody


def test_a_single_frame_scores_zero_liveness_however_good_it_looks():
    """The constant is the spec sentence. Every replayable signal at once, one frame: zero."""
    evidence = LivenessEvidence(signals=frozenset(REPLAYABLE | UNREPLAYABLE), frames=1)
    score, concerns = evidence.score()
    assert score == 0.0
    assert concerns


def test_the_matcher_refuses_before_matching_on_too_few_frames(enrolled):
    """Refused *before* matching rather than discounted after.

    A path that matches and then discards the result is a path somebody eventually optimises
    by skipping the check; a path that never matches cannot be.
    """
    matcher = FaceMatcher(ScriptedProvider(), enrolled)
    claim = matcher.identify(user_id="owner", frames=_frames(1), evidence=_live())
    assert claim.user_id is None
    assert claim.confidence == 0.0


@pytest.mark.parametrize("count", range(MIN_FRAMES))
def test_no_number_of_frames_below_the_floor_is_enough(enrolled, count):
    matcher = FaceMatcher(ScriptedProvider(), enrolled)
    claim = matcher.identify(user_id="owner", frames=_frames(count), evidence=_live())
    assert claim.user_id is None


# ================================================ §13/§14 photographs and replayed video


def test_a_perfect_match_with_no_liveness_is_refused(enrolled):
    """The photo attack, stated as a test. The provider says 1.0 — a printout matches better
    than a real face — and the answer is still no."""
    matcher = FaceMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(
        user_id="owner", frames=_frames(), evidence=LivenessEvidence(frames=10)
    )
    assert claim.user_id is None
    assert claim.confidence == 0.0
    assert claim.concerns


def test_everything_a_screen_can_produce_never_reaches_the_bar():
    """§14. A phone playing a video of the owner blinks, moves and varies over time. All
    three at full strength must stay below the threshold, because a ceiling on replayable
    evidence is the only thing that separates a video from a person."""
    evidence = LivenessEvidence(signals=frozenset(REPLAYABLE), frames=30)
    score, concerns = evidence.score()
    assert score < LIVE_ENOUGH
    assert any("screen" in c for c in concerns)
    assert evidence.live is False


def test_a_replayed_video_is_refused_by_the_matcher(enrolled):
    matcher = FaceMatcher(ScriptedProvider(match=0.99), enrolled)
    claim = matcher.identify(
        user_id="owner",
        frames=_frames(30),
        evidence=LivenessEvidence(signals=frozenset(REPLAYABLE), frames=30),
    )
    assert claim.user_id is None


def test_one_unreplayable_signal_outweighs_every_replayable_one():
    """The rule is about *kinds* of evidence, not quantity. Responding to an instruction
    chosen after the recording would have been made is worth more than any amount of
    blinking, and the scoring has to say so."""
    replayable = LivenessEvidence(signals=frozenset(REPLAYABLE), frames=30)
    challenged = LivenessEvidence(
        signals=frozenset({LivenessSignal.CHALLENGE_RESPONSE}), frames=MIN_FRAMES
    )
    assert challenged.score()[0] > replayable.score()[0]
    assert challenged.live is True


def test_every_liveness_signal_is_declared_replayable_or_not():
    """Adding a signal must force a decision about which side it falls on. A signal in
    neither set would silently count for nothing."""
    assert set(LivenessSignal) == REPLAYABLE | UNREPLAYABLE
    assert not (REPLAYABLE & UNREPLAYABLE)


def test_depth_and_infrared_are_treated_as_unreplayable():
    """§73's hardware-enhanced path. A screen has no shape and does not emit heat."""
    assert LivenessSignal.DEPTH in UNREPLAYABLE
    assert LivenessSignal.INFRARED in UNREPLAYABLE


def test_nothing_at_all_is_not_alive():
    assert LivenessEvidence(frames=30).live is False
    assert LivenessEvidence().live is False


# ==================================================== the refusal is not a quiet downgrade


def test_a_failed_liveness_check_reports_why_rather_than_lowering_confidence(enrolled):
    """§13 says be less sure or ask for another factor — not accept quietly. The claim
    carries the concern so the fusion engine can decide what would close the gap."""
    matcher = FaceMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(
        user_id="owner",
        frames=_frames(30),
        evidence=LivenessEvidence(signals=frozenset(REPLAYABLE), frames=30),
    )
    assert claim.user_id is None
    assert claim.confidence == 0.0, "a doubted match must not survive as a smaller number"
    assert claim.concerns


def test_liveness_and_confidence_are_never_blended(enrolled):
    """Separate fields, all the way through. One score would rank a good photograph above a
    slightly-off real face, which is exactly backwards."""
    from thursday_security.identity import IdentityClaim

    assert "confidence" in IdentityClaim.__annotations__
    assert "liveness" in IdentityClaim.__annotations__

    matcher = FaceMatcher(ScriptedProvider(match=0.7), enrolled)
    claim = matcher.identify(user_id="owner", frames=_frames(), evidence=_live())
    assert claim.confidence == pytest.approx(0.7)
    assert claim.liveness != claim.confidence


# ======================================================= no recogniser means no authentication


def test_with_no_recogniser_nobody_is_recognised(enrolled):
    """The most important test in this sprint.

    A stub returning a plausible confidence would be a security hole shaped like a feature —
    whoever deployed it would believe they had face recognition, and would in fact have a
    lock that opens for everybody while reporting that it is locked.
    """
    matcher = FaceMatcher(NoFaceRecognition(), enrolled)
    claim = matcher.identify(user_id="owner", frames=_frames(), evidence=_live())
    assert claim.user_id is None
    assert claim.confidence == 0.0


def test_the_absent_recogniser_says_it_is_absent():
    """§72's capability detection reads this, and §71 skips the biometric offer on it."""
    assert NoFaceRecognition().available is False
    assert NoFaceRecognition().name == "none"


def test_matching_with_no_recogniser_returns_no_match_rather_than_raising():
    """Deliberate. Matching sits on the authentication path, and a path that raises is one
    somebody wraps in a try/except that swallows it into a success. "No match" is the same
    answer and cannot be caught into a different one."""
    assert NoFaceRecognition().match_identity(object(), b"T") == 0.0


def test_enrolling_with_no_recogniser_refuses_loudly():
    """Enrolment is not on the authentication path, so here raising is right: somebody is
    setting this up and should be told it cannot be set up."""
    with pytest.raises(BiometricError):
        NoFaceRecognition().extract_template([object()])


# ================================================================== §7 enrolment coverage


def test_enrolment_needs_every_pose():
    """A template built from one straight-on angle fails the first time the owner tilts
    their head — and a system that refuses the owner teaches them to switch it off."""
    enrolment = FaceEnrollment(user_id="owner")
    enrolment.begin_sitting()
    for _ in range(FRAMES_PER_POSE):
        enrolment.add(Pose.FRONT, object())

    assert enrolment.complete is False
    assert enrolment.missing() == set(REQUIRED_POSES) - {Pose.FRONT}

    with pytest.raises(BiometricError):
        enrolment.finish(ScriptedProvider())


def test_enrolment_needs_more_than_one_frame_per_pose():
    enrolment = FaceEnrollment(user_id="owner")
    enrolment.begin_sitting()
    enrolment.begin_sitting()
    for pose in Pose:
        enrolment.add(pose, object())
    assert enrolment.complete is False


def test_enrolment_needs_more_than_one_sitting():
    """§7's several lighting conditions. Thursday cannot measure a room, but it can notice
    whether enrolment happened more than once."""
    enrolment = _full_enrolment(sittings=1)
    assert enrolment.complete is False
    assert _full_enrolment(sittings=MIN_LIGHTING_CONDITIONS).complete is True


def test_a_finished_enrolment_forgets_the_frames():
    """§7: the raw images are deleted once the template exists."""
    enrolment = _full_enrolment()
    assert enrolment.finish(ScriptedProvider()) == b"TEMPLATE"
    assert enrolment.covered() == set()
    assert enrolment.complete is False


def test_a_failed_enrolment_also_forgets_the_frames():
    """The `finally`, and the reason for it: an enrolment that raises partway must not leave
    a collection of the owner's face behind."""

    class Broken(ScriptedProvider):
        def extract_template(self, frames):
            raise RuntimeError("model died")

    enrolment = _full_enrolment()
    with pytest.raises(RuntimeError):
        enrolment.finish(Broken())
    assert enrolment.covered() == set()


def test_there_is_no_way_to_read_a_frame_back_out():
    """Deletion is guaranteed by nobody having an accessor, not by remembering to call one."""
    public = {name for name in dir(FaceEnrollment) if not name.startswith("_")}
    for accessor in ("frames", "samples", "images", "get_frame", "export"):
        assert accessor not in public


def test_an_empty_template_from_a_provider_is_refused():
    class Empty(ScriptedProvider):
        def extract_template(self, frames):
            return b""

    with pytest.raises(BiometricError):
        _full_enrolment().finish(Empty())


def _full_enrolment(*, sittings: int = MIN_LIGHTING_CONDITIONS) -> FaceEnrollment:
    enrolment = FaceEnrollment(user_id="owner")
    for _ in range(sittings):
        enrolment.begin_sitting()
    for pose in Pose:
        for _ in range(FRAMES_PER_POSE):
            enrolment.add(pose, object())
    return enrolment


# ========================================================================= §14 challenges


def test_a_challenge_asks_for_something_physical_and_immediate():
    instruction = challenge_for("nonce-1")
    assert instruction
    assert any(word in instruction for word in ("หัน", "ก้ม", "เงย"))


def test_different_nonces_can_produce_different_challenges():
    """A fixed challenge is one an attacker records once. This does not prove the
    distribution is good — it proves the function depends on its input at all."""
    assert len({challenge_for(f"n{i}") for i in range(50)}) > 1


# ============================================================== §55 nothing else inferred


def test_the_face_layer_infers_no_attribute_about_a_person():
    """Poses exist to make matching robust across angles, not to build a richer picture of
    anybody. Checked over identifiers rather than text, for the reason Sprint 73 records."""
    import ast

    from thursday_security import face

    tree = ast.parse(Path(face.__file__).read_text())
    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            identifiers.add(node.name)
        elif isinstance(node, ast.arg):
            identifiers.add(node.arg)

    lowered = {name.lower() for name in identifiers}
    for attribute in ("emotion", "gender", "ethnicity", "age", "mood", "expression"):
        assert not any(attribute in name for name in lowered), attribute


def test_the_matcher_never_returns_a_frame(enrolled):
    """Whatever else a claim carries, it is not a picture of anybody."""
    matcher = FaceMatcher(ScriptedProvider(), enrolled)
    claim = matcher.identify(user_id="owner", frames=_frames(), evidence=_live())
    fields = set(type(claim).__annotations__)
    for forbidden in ("frame", "frames", "image", "template", "sample"):
        assert forbidden not in fields


def test_identify_takes_evidence_rather_than_a_liveness_verdict():
    """A caller that could pass `live=True` is a caller that will. The matcher takes the
    observations and scores them itself — the same shape as Sprint 69's lesson runner."""
    parameters = set(inspect.signature(FaceMatcher.identify).parameters)
    assert parameters == {"self", "user_id", "frames", "evidence"}
    for forbidden in ("live", "is_live", "trusted", "verified", "skip_liveness"):
        assert forbidden not in parameters
