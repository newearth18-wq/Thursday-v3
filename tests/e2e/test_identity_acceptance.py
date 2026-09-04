"""The identity acceptance tests (BIOMETRIC IDENTITY §52, §76–§82, §89) — Sprint 79.

The spec writes these itself, which is the best kind: they judge whether the seven sprints
*compose* rather than whether each part works alone. Every one of them runs the whole chain —
matcher → fusion → session → gate — because that is where a defence gets lost, at a join
somebody reasoned about twice and connected once.

    §76  owner asks for something ordinary            → it happens
    §77  unknown person asks for the owner's files    → refused, and nothing leaks
    §78  attacker shows a photograph                  → liveness fails, denied
    §79  attacker plays a recording                   → external action blocked
    §80  owner walks away                             → private session locked
    §81  owner returns                                → work continues
    §82  camera broken                                → a fallback exists

**What these cannot prove.** There is no camera and no microphone in this container, so the
frames and samples are Python objects and the providers return numbers a test chose. These
prove the *policy composes*: that a perfect match with no liveness is refused all the way
through to the gate, that a locked session cannot be inherited, that a refusal says nothing.
They prove nothing about whether a real recogniser tells two people apart. That is the gap,
it is the whole reason `NoFaceRecognition` refuses rather than pretends, and it stays open
until this runs on hardware.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.biometrics import SecureBiometricStore
from thursday_security.face import (
    REPLAYABLE,
    FaceMatcher,
    LivenessEvidence,
    LivenessSignal,
    NoFaceRecognition,
)
from thursday_security.fusion import IdentityFusionEngine
from thursday_security.gate import CANNOT_VERIFY, IdentityGate
from thursday_security.identity import (
    AuthenticationSession,
    AuthLevel,
    Factor,
    UserKind,
)
from thursday_security.presence import (
    AWAY_BEFORE_LOCK,
    Observation,
    PresenceMonitor,
    SessionGuard,
)
from thursday_security.recovery_identity import RecoveryService
from thursday_security.voice import SpeakerMatcher, VoiceEvidence
from thursday_shared.enums import RiskLevel

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

FACE_TEMPLATE = b"OWNER-FACE"
VOICE_TEMPLATE = b"OWNER-VOICE"


class Recogniser:
    """Stands in for both providers. Returns what the scenario says it sees.

    `match` is deliberately settable to 1.0, because that is what a photograph and a
    recording actually produce — the attacks in §78 and §79 are not near-misses.
    """

    name = "scripted"
    available = True

    def __init__(self, *, match: float = 0.95) -> None:
        self.match = match

    # face
    def detect_face(self, frame):
        return True

    def extract_template(self, frames):
        return FACE_TEMPLATE

    def match_identity(self, frame, template):
        return self.match if template == FACE_TEMPLATE else 0.0

    def perform_liveness_check(self, frames):
        return 1.0

    # voice
    def detect_speech(self, audio):
        return True

    def extract_speaker_template(self, samples):
        return VOICE_TEMPLATE

    def match_speaker(self, audio, template):
        return self.match if template == VOICE_TEMPLATE else 0.0

    def perform_voice_liveness(self, audio):
        return 1.0

    def detect_replay_risk(self, audio):
        return 0.0

    def detect_synthetic_voice_risk(self, audio):
        return 0.0


@pytest.fixture
def store(tmp_path) -> SecureBiometricStore:
    store = SecureBiometricStore(directory=tmp_path / "bio")
    store.store_template(user_id="owner", kind="face", template=FACE_TEMPLATE, provider="s")
    store.store_template(user_id="owner", kind="voice", template=VOICE_TEMPLATE, provider="s")
    return store


class Stack:
    """The whole chain, wired the way the container would wire it.

    Assembled here rather than mocked because the acceptance tests exist to catch a defence
    that is lost *between* components — the join nobody tested.
    """

    def __init__(self, store, *, recogniser=None):
        provider = recogniser or Recogniser()
        self.faces = FaceMatcher(provider, store)
        self.voices = SpeakerMatcher(provider, store)
        self.fusion = IdentityFusionEngine()
        self.monitor = PresenceMonitor()
        self.guard = SessionGuard(monitor=self.monitor)
        self.gate = IdentityGate()
        self.session: AuthenticationSession | None = None

    def authenticate(self, *, claims, device_trusted=False, now=NOW):
        fused = self.fusion.fuse(claims, device_trusted=device_trusted)
        if not fused.identified:
            self.session = None
            return fused
        self.session = AuthenticationSession(
            user_id=fused.user_id,
            kind=UserKind.OWNER,
            auth_level=fused.level,
            factors=set(fused.factors),
            started_at=now,
            last_verified_at=now,
            last_activity_at=now,
        )
        return fused

    def may(self, action, risk, *, now=NOW):
        return self.gate.check(action=action, risk=risk, session=self.session, now=now)


def _live_frames(n: int = 10) -> list[object]:
    return [object() for _ in range(n)]


def _live_face() -> LivenessEvidence:
    return LivenessEvidence(
        signals=frozenset({LivenessSignal.BLINK, LivenessSignal.CHALLENGE_RESPONSE}),
        frames=10,
        challenge_passed=True,
    )


def _live_voice() -> VoiceEvidence:
    return VoiceEvidence(liveness=0.9, replay_risk=0.0, synthetic_risk=0.0)


# ============================================================== §76 the owner, ordinarily


def test_the_owner_asks_for_something_ordinary_and_gets_it(store):
    """§76. The test that stops all of this being a system nobody can use."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    fused = stack.authenticate(claims=[face], device_trusted=True)

    assert fused.identified
    assert stack.may("app.open", RiskLevel.LOW).sufficient is True


def test_the_owner_is_not_re_challenged_for_every_command(store):
    """§24. A system that asks constantly is one people switch off, and a switched-off system
    protects nobody."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    stack.authenticate(claims=[face], device_trusted=True)

    for minute in range(5):
        at = NOW + timedelta(minutes=minute)
        assert stack.may("app.open", RiskLevel.LOW, now=at).sufficient is True


# ================================================================ §77 an unknown person


def test_an_unknown_person_asking_for_the_owners_files_is_refused_and_told_nothing(store):
    """§77, and §30's list of what must not leak: who the owner is, file names, counts, the
    schedule. One sentence, and it is the same sentence every time."""
    stack = Stack(store)
    fused = stack.authenticate(claims=[])
    assert fused.identified is False

    verdict = stack.may("file.read", RiskLevel.MEDIUM)
    assert verdict.sufficient is False
    assert verdict.message == CANNOT_VERIFY

    rendered = f"{verdict.message}{fused.user_id or ''}"
    for leak in ("owner", "Office-PC", ".xlsx", "file"):
        assert leak not in rendered


def test_a_stranger_learns_nothing_from_how_the_refusal_differs(store):
    """The refusal for a file that would exist and one that would not is identical, because
    a difference is a file listing."""
    stack = Stack(store)
    stack.authenticate(claims=[])
    a = stack.may("file.read", RiskLevel.MEDIUM)
    b = stack.may("email.send", RiskLevel.HIGH)
    assert a.message == b.message == CANNOT_VERIFY


# ==================================================================== §78 a photograph


def test_a_photograph_of_the_owner_is_denied_all_the_way_through(store):
    """§78, and the composition test that matters most.

    The provider reports a **perfect** match, because that is what a printout produces — it
    is a better match than the owner at an angle in bad light. Every layer has to refuse it,
    and the last one has to refuse it too.
    """
    stack = Stack(store, recogniser=Recogniser(match=1.0))
    photo = stack.faces.identify(
        user_id="owner", frames=_live_frames(30), evidence=LivenessEvidence(frames=30)
    )
    assert photo.user_id is None

    fused = stack.authenticate(claims=[photo])
    assert fused.identified is False
    assert stack.may("file.read", RiskLevel.MEDIUM).sufficient is False


def test_a_video_of_the_owner_played_from_a_phone_is_denied(store):
    """§52's "phone video owner → reject/high risk". It blinks, it moves, it varies over
    time — every replayable signal — and it is still a screen."""
    stack = Stack(store, recogniser=Recogniser(match=1.0))
    replayed = stack.faces.identify(
        user_id="owner",
        frames=_live_frames(30),
        evidence=LivenessEvidence(signals=frozenset(REPLAYABLE), frames=30),
    )
    fused = stack.authenticate(claims=[replayed])
    assert fused.identified is False


# ================================================================== §79 a recorded voice


def test_a_recording_of_the_owner_cannot_send_an_email(store):
    """§79. The recording *is* the owner's voice and matches perfectly, so matching is not
    what stops it."""
    stack = Stack(store, recogniser=Recogniser(match=1.0))
    recorded = stack.voices.identify(
        user_id="owner",
        audio=object(),
        evidence=VoiceEvidence(liveness=0.9, replay_risk=0.9),
        now=NOW,
    )
    assert recorded.user_id is None

    fused = stack.authenticate(claims=[recorded])
    assert fused.identified is False
    assert stack.may("email.send", RiskLevel.HIGH).sufficient is False


def test_even_a_live_voice_alone_cannot_send_an_email(store):
    """§17, composed. Not an attack — the owner really is speaking — and voice alone still
    does not reach an external action, because a clone would sound the same."""
    stack = Stack(store)
    voice = stack.voices.identify(user_id="owner", audio=object(), evidence=_live_voice(), now=NOW)
    fused = stack.authenticate(claims=[voice])

    assert fused.identified is True, "the owner is recognised"
    assert stack.may("email.send", RiskLevel.HIGH).sufficient is False, "and still not enough"
    assert fused.next_factor is Factor.FACE


def test_adding_a_face_to_the_voice_is_enough(store):
    """The other half: the remedy §18 offers actually works, or the refusal is a dead end."""
    stack = Stack(store)
    voice = stack.voices.identify(user_id="owner", audio=object(), evidence=_live_voice(), now=NOW)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    stack.authenticate(claims=[voice, face], device_trusted=True)

    assert stack.may("email.send", RiskLevel.HIGH).sufficient is True


# ============================================================= §64 the mixed attack


def test_an_unknown_face_playing_the_owners_recording_identifies_nobody(store):
    """§64, end to end. A stranger sits at the machine and plays the owner speaking."""

    # Modelled the way it really happens: the face in front of the camera is compared against
    # the *owner's* template and does not match, while a recording supplies the owner's voice.
    class StrangerAtTheKeyboard(Recogniser):
        def match_identity(self, frame, template):
            return 0.05  # a living face, and not this one

    stack = Stack(store, recogniser=StrangerAtTheKeyboard(match=1.0))
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    owner_voice = stack.voices.identify(
        user_id="owner", audio=object(), evidence=_live_voice(), now=NOW
    )

    assert face.user_id is None
    assert face.observed is True, "a face was there — that is the fact that must contradict"
    assert owner_voice.user_id == "owner"

    fused = stack.authenticate(claims=[face, owner_voice])

    assert fused.identified is False
    assert any("did not match" in r for r in fused.reasons)
    assert stack.may("file.read", RiskLevel.MEDIUM).sufficient is False


def test_nobody_in_frame_is_not_the_same_as_the_wrong_person_in_frame(store):
    """The distinction the fix turns on, stated on its own.

    The owner speaking to a machine with no camera pointed at them must still work — §47's
    dark room, §48's broken camera. Only an *observed* mismatch contradicts.
    """
    stack = Stack(store)
    voice = stack.voices.identify(user_id="owner", audio=object(), evidence=_live_voice(), now=NOW)
    # No face claim at all: nothing was looked at.
    fused = stack.authenticate(claims=[voice])
    assert fused.identified is True
    assert fused.level is AuthLevel.SINGLE


# ========================================================== §80/§81 away, and back


def test_the_owner_walks_away_and_their_session_cannot_be_inherited(store):
    """§80. No attack was mounted and nothing was spoofed — the owner just left."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    stack.authenticate(claims=[face], device_trusted=True)
    stack.monitor.observe(Observation(at=NOW, recognised=frozenset({"owner"})))
    stack.guard.apply(stack.session, now=NOW)

    assert stack.may("file.read", RiskLevel.MEDIUM).sufficient is True

    gone = NOW + AWAY_BEFORE_LOCK + timedelta(seconds=1)
    stack.guard.apply(stack.session, now=gone)

    assert stack.may("file.read", RiskLevel.MEDIUM, now=gone).sufficient is False
    assert stack.may("app.open", RiskLevel.LOW, now=gone).sufficient is False


def test_somebody_else_sitting_down_ends_it_immediately(store):
    """§80's sharper half. Not a degrade — a stranger at the keyboard must not inherit even
    a reduced session."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    stack.authenticate(claims=[face], device_trusted=True)
    stack.monitor.observe(Observation(at=NOW, recognised=frozenset({"owner"})))
    stack.guard.apply(stack.session, now=NOW)

    later = NOW + timedelta(seconds=10)
    stack.monitor.observe(Observation(at=later, recognised=frozenset(), unknown_people=1))
    stack.guard.apply(stack.session, now=later + timedelta(seconds=1))

    assert stack.session.ended_reason
    assert stack.may("app.open", RiskLevel.LOW, now=later).sufficient is False


def test_the_owner_returns_and_work_continues(store):
    """§81. The same session, so anything running carries on and the audit trail stays one
    thread — but the level comes from a fresh check, because "they came back" is a claim
    about a person."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    stack.authenticate(claims=[face], device_trusted=True)
    original = stack.session.session_id

    stack.monitor.observe(Observation(at=NOW, recognised=frozenset({"owner"})))
    stack.guard.apply(stack.session, now=NOW)
    gone = NOW + AWAY_BEFORE_LOCK + timedelta(seconds=1)
    stack.guard.apply(stack.session, now=gone)

    back = gone + timedelta(minutes=1)
    fresh = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    refused = stack.fusion.fuse([fresh], device_trusted=True)
    stack.session.ended_reason = ""
    stack.monitor.observe(Observation(at=back, recognised=frozenset({"owner"})))
    stack.guard.restore(stack.session, level=refused.level, now=back)
    stack.guard.apply(stack.session, now=back)

    assert stack.session.session_id == original
    assert stack.may("file.read", RiskLevel.MEDIUM, now=back).sufficient is True


# ======================================================== §82 the camera is broken


def test_a_broken_camera_does_not_lock_the_owner_out(store):
    """§48 and §82. Camera unavailable must not fail the whole of Thursday, and §44's
    fallback is what makes that true rather than a hope."""
    stack = Stack(store, recogniser=NoFaceRecognition())
    dead = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    assert dead.user_id is None

    recovery = RecoveryService()
    recovery.set_pin("841302")
    outcome = recovery.with_pin("841302", now=NOW)

    assert outcome.ok is True
    assert outcome.level > AuthLevel.NONE


def test_the_fallback_is_offered_rather_than_the_owner_being_stranded(store):
    """§82's flow: camera broken, voice insufficient for the risk, so Thursday offers
    something else. `next_factor` is that offer, and it must not be the thing that just
    failed."""
    stack = Stack(store)
    voice = stack.voices.identify(user_id="owner", audio=object(), evidence=_live_voice(), now=NOW)
    fused = stack.authenticate(claims=[voice])

    assert stack.may("file.delete", RiskLevel.CRITICAL).sufficient is False
    assert fused.next_factor is not Factor.VOICE


# ============================================================== §66 stopping is for all


def test_anybody_can_stop_thursday(store):
    """§66. An emergency stop that required authentication would make an attacker's best
    move "cause identification to fail"."""
    stack = Stack(store)
    stack.authenticate(claims=[])
    assert stack.session is None
    assert stack.may("system.stop", RiskLevel.CRITICAL).sufficient is True


def test_but_not_start_things_again(store):
    """§66's distinction: stopping reduces risk, starting does not."""
    stack = Stack(store)
    stack.authenticate(claims=[])
    assert stack.may("system.process.start", RiskLevel.HIGH).sufficient is False


# ================================================= §9/§59/§75 nothing leaks out of the layer


def test_nothing_an_agent_receives_mentions_a_biometric(store):
    """§59 and §61, at the end of the chain rather than in the type's own test."""
    stack = Stack(store)
    face = stack.faces.identify(user_id="owner", frames=_live_frames(), evidence=_live_face())
    voice = stack.voices.identify(user_id="owner", audio=object(), evidence=_live_voice(), now=NOW)
    stack.authenticate(claims=[face, voice], device_trusted=True)

    context = stack.session.context(now=NOW).to_dict()
    rendered = str(context).lower()
    for forbidden in ("face", "voice", "template", "confidence", "liveness", "frame"):
        assert forbidden not in rendered


def test_no_template_reaches_a_gate_verdict(store):
    """§75: never log a biometric template. The verdict is what an audit entry is built
    from, so it is where one would end up."""
    stack = Stack(store, recogniser=Recogniser(match=1.0))
    photo = stack.faces.identify(
        user_id="owner", frames=_live_frames(30), evidence=LivenessEvidence(frames=30)
    )
    stack.authenticate(claims=[photo])
    verdict = stack.may("file.read", RiskLevel.MEDIUM)

    rendered = str(verdict).encode()
    assert FACE_TEMPLATE not in rendered
    assert VOICE_TEMPLATE not in rendered
