"""Thursday's voice (V4, §6).

One voice, six modes. The modes are not decoration: they are how a spoken reply carries the
thing a written one carries with formatting. "Done" and "Done, but I could not verify it"
should not sound the same, and in speech the only way to say so is prosody.

The profile is a value object rather than a dict so that the mapping from `VoiceMode` to
delivery lives in one place, and so a provider that supports fewer knobs can ignore the
ones it cannot honour without every caller having to know which.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from thursday_shared.enums import VoiceMode


@dataclass(frozen=True)
class VoiceProfile:
    """How Thursday sounds in one mode."""

    tone: str = "composed"
    pace: str = "medium"
    pitch: str = "medium_low"
    energy: str = "controlled"
    style: str = "concise"
    #: "auto" follows the language of the request. Thai and English are both native.
    language: str = "auto"

    #: Numeric hints for providers that take them. Derived from the words above so the two
    #: cannot drift: a profile that says "urgent" and synthesises at 0.9× would be lying.
    rate: float = 1.0
    pitch_shift: float = 0.0
    volume: float = 1.0

    def for_language(self, language: str) -> VoiceProfile:
        return replace(self, language=language)

    def as_synthesis_args(self) -> dict[str, float | str]:
        return {
            "rate": self.rate,
            "pitch": self.pitch_shift,
            "volume": self.volume,
            "style": self.style,
            "language": self.language,
        }


#: The base identity. Every mode is a departure from this, not a separate voice — the owner
#: should hear one assistant whose delivery changes, not six characters.
THURSDAY_VOICE = VoiceProfile()

PROFILES: dict[VoiceMode, VoiceProfile] = {
    VoiceMode.NORMAL: THURSDAY_VOICE,
    # Working through something: slower, quieter, less inflection. Signals "not finished".
    VoiceMode.THINKING: replace(
        THURSDAY_VOICE,
        tone="focused",
        pace="slow",
        energy="low",
        style="quiet",
        rate=0.92,
        pitch_shift=-0.05,
        volume=0.8,
    ),
    # Done and verified. Brief and slightly lifted — the shortest utterance of the six,
    # because a success that takes ten seconds to announce is not a success.
    VoiceMode.SUCCESS: replace(
        THURSDAY_VOICE, style="brief", rate=1.02, pitch_shift=0.05, volume=1.0
    ),
    # Something needs attention: firmer and marginally louder, never alarmed.
    VoiceMode.WARNING: replace(
        THURSDAY_VOICE,
        tone="firm",
        style="firm",
        energy="raised",
        rate=0.95,
        pitch_shift=-0.03,
        volume=1.05,
    ),
    # Reserved for things that cannot wait. Clipped, faster, louder.
    VoiceMode.URGENT: replace(
        THURSDAY_VOICE,
        tone="direct",
        pace="fast",
        style="clipped",
        energy="high",
        rate=1.08,
        pitch_shift=0.08,
        volume=1.1,
    ),
    # Someone else is present, or it is late. Terse and low — this mode is why the
    # composer knows how many people are in the room (§43).
    VoiceMode.QUIET: replace(
        THURSDAY_VOICE,
        pace="measured",
        energy="minimal",
        style="terse",
        rate=0.97,
        pitch_shift=-0.02,
        volume=0.55,
    ),
}


def profile_for(mode: VoiceMode | str, *, language: str | None = None) -> VoiceProfile:
    """The profile for a mode, defaulting to NORMAL for anything unrecognised.

    Unrecognised falls back rather than raising: a new `VoiceMode` added elsewhere should
    make Thursday sound ordinary, not make it fall silent.
    """
    try:
        resolved = VoiceMode(str(mode))
    except ValueError:
        resolved = VoiceMode.NORMAL
    profile = PROFILES.get(resolved, THURSDAY_VOICE)
    return profile.for_language(language) if language else profile
