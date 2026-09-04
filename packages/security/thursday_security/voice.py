"""Voices: enrolling one, and never trusting one on its own (§8, §16, §17, §85).

§17 is the rule this module is shaped around, and it is stronger than the face equivalent:

    "เสียงที่เหมือนเจ้าของไม่ได้หมายความว่าเป็นเจ้าของเสมอ …
     สำหรับ High-risk Action: Voice-only authentication ห้ามเพียงพอ"

A face has to be presented to a camera. A voice arrives through the air from anywhere in the
room, survives being recorded on any phone, and can now be synthesised from a few seconds of
the owner speaking at a conference. It is the weakest of the biometrics and the easiest to
capture without the owner noticing, so `VOICE_ALONE_CEILING` exists: no amount of voice
evidence, at any confidence, reaches past one factor. That is not a tuning parameter — it is
the spec's rule expressed as a constant the fusion engine cannot route around.

**A challenge is about time, not just content.** §16 asks Thursday to say "พูดว่า 7 2 9" and
check the speaker, the phrase, the timing and the liveness. The timing is the part that does
the work: a recording of the owner saying "7 2 9" exists only if somebody knew to ask for
"7 2 9", and the window is short enough that synthesising one after hearing the challenge is
a race rather than a formality. So a challenge expires, and a correct answer that arrives late
is a wrong answer.

**Replay and synthesis are separate concerns from confidence**, for the same reason liveness
is on the face side: a recording of the owner *is* the owner's voice and matches perfectly,
and a clone is built to. Matching harder finds neither.

With no recogniser, `NoSpeakerRecognition` identifies nobody — the `NoKeychain` posture again.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger

from thursday_security.biometrics import BiometricError
from thursday_security.identity import AuthLevel, Factor, IdentityClaim

log = get_logger(__name__)


#: §17, as a constant. Voice evidence alone never counts for more than a single factor
#: whatever its confidence, because the thing it is evidence *of* — that a voice matching the
#: owner's reached the microphone — is true of a recording and true of a clone.
#:
#: The fusion engine imports this rather than deciding it, so raising the ceiling means
#: editing the line the spec is quoted against.
VOICE_ALONE_CEILING = AuthLevel.SINGLE


class Condition(StrEnum):
    """§8's enrolment coverage. How the owner speaks, not what they sound like."""

    NORMAL = "NORMAL"
    QUIET = "QUIET"
    LOUD = "LOUD"
    SHORT = "SHORT"
    LONG = "LONG"


#: Every condition, because a template built only from careful studio-quality sentences fails
#: the owner the first time they mumble — and a system that fails the owner gets turned off.
REQUIRED_CONDITIONS: frozenset[Condition] = frozenset(Condition)

#: §8 asks for Thai, and English too if the owner uses it. Thai is required because it is the
#: configured locale; English is recorded when offered and never demanded.
REQUIRED_LANGUAGES: frozenset[str] = frozenset({"th"})

SAMPLES_PER_CONDITION = 2

#: How long a challenge answer stays acceptable. Short on purpose: this window is the gap in
#: which somebody would have to hear the challenge, synthesise the owner saying it, and play
#: it back. Long enough that a person can read four digits aloud without being rushed.
CHALLENGE_WINDOW = timedelta(seconds=20)

#: Above these, the sample is treated as a recording or a synthesis rather than a person.
#: Separate thresholds because they are different attacks with different tells.
MAX_REPLAY_RISK = 0.4
MAX_SYNTHETIC_RISK = 0.4

#: Below this the voice is not treated as live at all.
LIVE_ENOUGH = 0.6


@dataclass(frozen=True)
class VoiceChallenge:
    """§16. Something to say, chosen now, that expires.

    The nonce is what makes a recording useless: an attacker with hours of the owner speaking
    still does not have them saying *this*, and has `CHALLENGE_WINDOW` to produce it.
    """

    phrase: str
    issued_at: datetime
    nonce: str

    def expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) - self.issued_at > CHALLENGE_WINDOW

    def satisfied_by(self, said: str, *, now: datetime | None = None) -> tuple[bool, str]:
        """Whether this answer counts. Content and timing, both."""
        if self.expired(now=now):
            # A correct answer that arrives late is a wrong answer: the delay is exactly the
            # room a synthesis needs.
            return False, "the answer came too late to be an answer to this question"
        if _normalise(said) != _normalise(self.phrase):
            return False, "that was not what was asked for"
        return True, ""


def _normalise(text: str) -> str:
    """Compare what was said, not how it was transcribed."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


#: The words a challenge is built from. Digits, because they are unambiguous to transcribe in
#: both languages and short enough that the window is comfortable for a person.
_DIGIT_WORDS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")


def new_challenge(*, digits: int = 3, now: datetime | None = None) -> VoiceChallenge:
    """Issue one. Random from the system CSPRNG rather than from `random`.

    `secrets`, not `random`: a predictable challenge is one an attacker can have recorded in
    advance, which is the whole thing a challenge exists to prevent.
    """
    chosen = [secrets.randbelow(10) for _ in range(digits)]
    phrase = " ".join(_DIGIT_WORDS[d] for d in chosen)
    return VoiceChallenge(
        phrase=phrase,
        issued_at=now or datetime.now(UTC),
        nonce=secrets.token_hex(8),
    )


@dataclass(frozen=True)
class VoiceEvidence:
    """What was heard, beyond how well it matched."""

    #: 0–1, from the provider. How much this sounded like a person speaking now.
    liveness: float = 0.0
    #: 0–1. How much this looked like a played-back recording (§17).
    replay_risk: float = 0.0
    #: 0–1. How much this looked like a generated voice (§17).
    synthetic_risk: float = 0.0
    #: The challenge answer, when one was asked for.
    challenge: VoiceChallenge | None = None
    said: str = ""

    def assess(self, *, now: datetime | None = None) -> tuple[bool, tuple[str, ...]]:
        """Whether to believe there is a person here, and what argues against it."""
        concerns: list[str] = []

        if self.replay_risk > MAX_REPLAY_RISK:
            concerns.append("this sounded like a recording")
        if self.synthetic_risk > MAX_SYNTHETIC_RISK:
            concerns.append("this sounded like a generated voice")
        if self.liveness < LIVE_ENOUGH:
            concerns.append("this did not sound like someone speaking now")

        if self.challenge is not None:
            ok, why = self.challenge.satisfied_by(self.said, now=now)
            if not ok:
                concerns.append(why)

        return (not concerns), tuple(concerns)


@dataclass
class VoiceEnrollment:
    """§8's enrolment. Several sentences, several ways of speaking, then forget the audio."""

    user_id: str
    _samples: dict[Condition, list[Any]] = field(default_factory=dict)
    _languages: set[str] = field(default_factory=set)

    def add(self, condition: Condition, sample: Any, *, language: str = "th") -> None:
        self._samples.setdefault(condition, []).append(sample)
        self._languages.add(language)

    def covered(self) -> set[Condition]:
        return {c for c, samples in self._samples.items() if len(samples) >= SAMPLES_PER_CONDITION}

    def missing(self) -> set[Condition]:
        return set(REQUIRED_CONDITIONS) - self.covered()

    @property
    def complete(self) -> bool:
        return not self.missing() and self._languages >= REQUIRED_LANGUAGES

    def finish(self, provider: Any) -> bytes:
        """Build the speaker template, then forget the audio (§8).

        Same `finally` as the face side, for the same reason: an enrolment that fails partway
        must not leave recordings of the owner behind.
        """
        if not self.complete:
            raise BiometricError(
                "voice enrolment is not finished: still need "
                f"{sorted(c.value for c in self.missing())}"
            )
        samples = [s for condition in Condition for s in self._samples.get(condition, [])]
        try:
            template = provider.extract_speaker_template(samples)
        finally:
            self._samples.clear()
        if not template:
            raise BiometricError("the voice provider produced no template")
        log.info("voice_enrolled", user=self.user_id, conditions=len(REQUIRED_CONDITIONS))
        return template


class NoSpeakerRecognition:
    """What a machine with no speaker recogniser gets. Identifies nobody.

    The `NoKeychain` posture, and the `NoFaceRecognition` reasoning: a plausible number here
    would be a lock that opens for everybody while reporting itself locked.
    """

    name = "none"
    available = False

    def detect_speech(self, audio: Any) -> bool:
        return False

    def extract_speaker_template(self, samples: list[Any]) -> bytes:
        raise BiometricError(
            "no speaker recogniser is installed on this machine, so no voice can be enrolled"
        )

    def match_speaker(self, audio: Any, template: bytes) -> float:
        return 0.0

    def perform_voice_liveness(self, audio: Any) -> float:
        return 0.0

    def detect_replay_risk(self, audio: Any) -> float:
        # Maximum suspicion, not zero. "I cannot tell whether this is a recording" is much
        # closer to "it might be" than to "it is not", and the fail-closed direction here is
        # the one where an unknown sample is treated as the attack.
        return 1.0

    def detect_synthetic_voice_risk(self, audio: Any) -> float:
        return 1.0


class SpeakerMatcher:
    """Turns audio into a claim, with replay and synthesis kept separate from confidence."""

    def __init__(self, provider: Any, store: Any) -> None:
        self._provider = provider
        self._store = store

    def identify(
        self, *, user_id: str, audio: Any, evidence: VoiceEvidence, now: datetime | None = None
    ) -> IdentityClaim:
        """Is this the enrolled speaker, and is it a person speaking now?

        Always returns a claim carrying `Factor.VOICE`, which the fusion engine will cap at
        `VOICE_ALONE_CEILING` — this layer does not know about levels and does not need to.
        """
        template = self._store.load_template(user_id=user_id, kind="voice")
        if template is None:
            return IdentityClaim(
                factor=Factor.VOICE, user_id=None, concerns=("no voice is enrolled",)
            )

        believable, concerns = evidence.assess(now=now)
        confidence = self._provider.match_speaker(audio, template)

        if not believable:
            log.info("voice_not_believable", user=user_id, concerns=len(concerns))
            return IdentityClaim(
                factor=Factor.VOICE,
                user_id=None,
                confidence=0.0,
                liveness=evidence.liveness,
                concerns=concerns,
            )

        return IdentityClaim(
            factor=Factor.VOICE,
            user_id=user_id if confidence > 0 else None,
            confidence=confidence,
            liveness=evidence.liveness,
            concerns=concerns,
        )
