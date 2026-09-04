"""Voices, and never trusting one on its own (BIOMETRIC IDENTITY §85) — Sprint 75.

§17 is stronger than anything on the face side: *"Voice-only authentication ห้ามเพียงพอ."*

A face must be presented to a camera. A voice arrives through the air from anywhere in the
room, survives being recorded on any phone, and can be synthesised from a few seconds of the
owner speaking in public. It is the weakest biometric and the easiest to capture without the
owner noticing — so the ceiling is a constant, not a threshold.

**What these tests cannot do.** No microphone, no speaker recogniser. Every sample below is a
Python object and the provider returns numbers a test chose. This proves the policy: the
ceiling, the challenge timing, replay and synthesis refusing regardless of match quality. It
proves nothing about whether a real recogniser distinguishes two speakers.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.biometrics import BiometricError, SecureBiometricStore
from thursday_security.identity import AuthLevel
from thursday_security.voice import (
    CHALLENGE_WINDOW,
    LIVE_ENOUGH,
    MAX_REPLAY_RISK,
    MAX_SYNTHETIC_RISK,
    REQUIRED_CONDITIONS,
    SAMPLES_PER_CONDITION,
    VOICE_ALONE_CEILING,
    Condition,
    NoSpeakerRecognition,
    SpeakerMatcher,
    VoiceChallenge,
    VoiceEnrollment,
    VoiceEvidence,
    new_challenge,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


class ScriptedProvider:
    """A stand-in. Returns what a test told it to, and is named so nobody mistakes it."""

    name = "scripted"
    available = True

    def __init__(self, *, match: float = 0.95) -> None:
        self._match = match

    def detect_speech(self, audio):
        return True

    def extract_speaker_template(self, samples):
        return b"VOICE-TEMPLATE"

    def match_speaker(self, audio, template):
        return self._match if template == b"VOICE-TEMPLATE" else 0.0

    def perform_voice_liveness(self, audio):
        return 1.0

    def detect_replay_risk(self, audio):
        return 0.0

    def detect_synthetic_voice_risk(self, audio):
        return 0.0


@pytest.fixture
def enrolled(tmp_path) -> SecureBiometricStore:
    store = SecureBiometricStore(directory=tmp_path / "bio")
    store.store_template(
        user_id="owner", kind="voice", template=b"VOICE-TEMPLATE", provider="scripted"
    )
    return store


def _good(**over) -> VoiceEvidence:
    return VoiceEvidence(**{"liveness": 0.9, "replay_risk": 0.0, "synthetic_risk": 0.0, **over})


# ============================================================ §17 the ceiling is a constant


def test_voice_alone_never_counts_for_more_than_one_factor():
    """§17 as a constant rather than a threshold. The fusion engine imports this, so raising
    it means editing the line the spec is quoted against."""
    assert VOICE_ALONE_CEILING is AuthLevel.SINGLE
    assert VOICE_ALONE_CEILING < AuthLevel.TWO_BIOMETRIC


def test_a_perfect_voice_match_is_still_only_one_factor(enrolled):
    """Confidence does not buy levels. A cloned voice matches perfectly by construction —
    that is what a clone is for."""
    matcher = SpeakerMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(user_id="owner", audio=object(), evidence=_good(), now=NOW)
    assert claim.user_id == "owner"
    assert claim.confidence == 1.0
    assert VOICE_ALONE_CEILING is AuthLevel.SINGLE


# ============================================================== §17 replay and synthesis


def test_a_recording_is_refused_however_well_it_matches(enrolled):
    """§79's acceptance test. The recording *is* the owner's voice, so matching is not what
    catches it."""
    matcher = SpeakerMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(
        user_id="owner",
        audio=object(),
        evidence=_good(replay_risk=MAX_REPLAY_RISK + 0.1),
        now=NOW,
    )
    assert claim.user_id is None
    assert claim.confidence == 0.0
    assert any("recording" in c for c in claim.concerns)


def test_a_synthesised_voice_is_refused_however_well_it_matches(enrolled):
    """§52's synthetic-voice case. A clone is built to match."""
    matcher = SpeakerMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(
        user_id="owner",
        audio=object(),
        evidence=_good(synthetic_risk=MAX_SYNTHETIC_RISK + 0.1),
        now=NOW,
    )
    assert claim.user_id is None
    assert any("generated" in c for c in claim.concerns)


def test_something_that_does_not_sound_live_is_refused(enrolled):
    matcher = SpeakerMatcher(ScriptedProvider(match=1.0), enrolled)
    claim = matcher.identify(
        user_id="owner", audio=object(), evidence=_good(liveness=LIVE_ENOUGH - 0.1), now=NOW
    )
    assert claim.user_id is None


def test_replay_and_synthesis_are_separate_risks():
    """Different attacks with different tells. One threshold for both would mean tuning for
    one and losing the other."""
    fields = set(VoiceEvidence.__annotations__)
    assert {"replay_risk", "synthetic_risk", "liveness"} <= fields
    assert MAX_REPLAY_RISK is not None and MAX_SYNTHETIC_RISK is not None


# ==================================================================== §16 the challenge


def test_a_challenge_answered_correctly_and_promptly_passes():
    challenge = new_challenge(now=NOW)
    ok, why = challenge.satisfied_by(challenge.phrase, now=NOW + timedelta(seconds=3))
    assert ok is True
    assert why == ""


def test_a_correct_answer_that_arrives_late_is_a_wrong_answer():
    """The timing is the part that does the work: the delay is exactly the room a synthesis
    needs to hear the challenge and produce it."""
    challenge = new_challenge(now=NOW)
    late = NOW + CHALLENGE_WINDOW + timedelta(seconds=1)
    ok, why = challenge.satisfied_by(challenge.phrase, now=late)
    assert ok is False
    assert "late" in why


def test_the_wrong_phrase_fails_even_in_time():
    challenge = VoiceChallenge(phrase="หนึ่ง สอง สาม", issued_at=NOW, nonce="n")
    ok, _ = challenge.satisfied_by("สี่ ห้า หก", now=NOW)
    assert ok is False


def test_transcription_differences_do_not_fail_a_correct_answer():
    """Compare what was said, not how it was transcribed. A challenge the owner fails on
    punctuation is one that trains them to retry until something passes."""
    challenge = VoiceChallenge(phrase="หนึ่ง สอง สาม", issued_at=NOW, nonce="n")
    ok, _ = challenge.satisfied_by("  หนึ่ง, สอง  สาม!  ", now=NOW)
    assert ok is True


def test_challenges_are_not_predictable():
    """A predictable challenge is one an attacker has recorded in advance."""
    phrases = {new_challenge(now=NOW).phrase for _ in range(200)}
    assert len(phrases) > 20


def test_challenges_come_from_the_system_csprng():
    """`secrets`, not `random`. Checked structurally, because the difference is invisible in
    behaviour until somebody is predicting them."""
    import ast
    from pathlib import Path

    from thursday_security import voice

    tree = ast.parse(Path(voice.__file__).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "secrets" in imported
    assert "random" not in imported


def test_a_failed_challenge_refuses_the_whole_claim(enrolled):
    matcher = SpeakerMatcher(ScriptedProvider(match=1.0), enrolled)
    challenge = new_challenge(now=NOW)
    claim = matcher.identify(
        user_id="owner",
        audio=object(),
        evidence=_good(challenge=challenge, said="something else"),
        now=NOW,
    )
    assert claim.user_id is None


def test_a_passed_challenge_lets_a_good_sample_through(enrolled):
    matcher = SpeakerMatcher(ScriptedProvider(), enrolled)
    challenge = new_challenge(now=NOW)
    claim = matcher.identify(
        user_id="owner",
        audio=object(),
        evidence=_good(challenge=challenge, said=challenge.phrase),
        now=NOW + timedelta(seconds=5),
    )
    assert claim.user_id == "owner"


# ======================================================= no recogniser means no identity


def test_with_no_recogniser_nobody_is_identified(enrolled):
    matcher = SpeakerMatcher(NoSpeakerRecognition(), enrolled)
    claim = matcher.identify(user_id="owner", audio=object(), evidence=_good(), now=NOW)
    assert claim.user_id is None


def test_an_absent_recogniser_reports_maximum_suspicion_not_zero():
    """The fail-closed direction. "I cannot tell whether this is a recording" is much closer
    to "it might be" than to "it is not", so an unknown sample is treated as the attack."""
    provider = NoSpeakerRecognition()
    assert provider.detect_replay_risk(object()) == 1.0
    assert provider.detect_synthetic_voice_risk(object()) == 1.0
    assert provider.perform_voice_liveness(object()) == 0.0


def test_enrolling_a_voice_with_no_recogniser_refuses_loudly():
    with pytest.raises(BiometricError):
        NoSpeakerRecognition().extract_speaker_template([object()])


# =========================================================== §8 enrolment coverage


def test_enrolment_needs_every_way_of_speaking():
    """§8 lists normal, quiet, loud, short and long. A template built only from careful
    sentences fails the owner the first time they mumble."""
    enrolment = VoiceEnrollment(user_id="owner")
    for _ in range(SAMPLES_PER_CONDITION):
        enrolment.add(Condition.NORMAL, object())
    assert enrolment.complete is False
    assert enrolment.missing() == set(REQUIRED_CONDITIONS) - {Condition.NORMAL}
    with pytest.raises(BiometricError):
        enrolment.finish(ScriptedProvider())


def test_enrolment_needs_more_than_one_sample_per_condition():
    enrolment = VoiceEnrollment(user_id="owner")
    for condition in Condition:
        enrolment.add(condition, object())
    assert enrolment.complete is False


def test_a_finished_enrolment_forgets_the_audio():
    """§8: the raw audio goes once the template exists."""
    enrolment = _full()
    assert enrolment.finish(ScriptedProvider()) == b"VOICE-TEMPLATE"
    assert enrolment.covered() == set()


def test_a_failed_enrolment_also_forgets_the_audio():
    class Broken(ScriptedProvider):
        def extract_speaker_template(self, samples):
            raise RuntimeError("model died")

    enrolment = _full()
    with pytest.raises(RuntimeError):
        enrolment.finish(Broken())
    assert enrolment.covered() == set()


def test_there_is_no_way_to_read_a_sample_back_out():
    public = {name for name in dir(VoiceEnrollment) if not name.startswith("_")}
    for accessor in ("samples", "audio", "recordings", "get_sample", "export"):
        assert accessor not in public


def _full() -> VoiceEnrollment:
    enrolment = VoiceEnrollment(user_id="owner")
    for condition in Condition:
        for _ in range(SAMPLES_PER_CONDITION):
            enrolment.add(condition, object(), language="th")
    return enrolment


# ================================================================== the shape of the API


def test_identify_takes_evidence_rather_than_a_verdict():
    """No `trusted=`, no `skip_replay_check=`. The same rule as the face matcher and the
    lesson runner: a caller that could assert success is one that will."""
    parameters = set(inspect.signature(SpeakerMatcher.identify).parameters)
    assert parameters == {"self", "user_id", "audio", "evidence", "now"}
    for forbidden in ("trusted", "verified", "skip_replay_check", "live", "ok"):
        assert forbidden not in parameters


def test_a_claim_never_carries_audio(enrolled):
    matcher = SpeakerMatcher(ScriptedProvider(), enrolled)
    claim = matcher.identify(user_id="owner", audio=object(), evidence=_good(), now=NOW)
    fields = set(type(claim).__annotations__)
    for forbidden in ("audio", "sample", "template", "waveform", "recording"):
        assert forbidden not in fields
