"""Thursday's voice (V4).

    wake word → microphone → VAD → STT → Core → verification → TTS → speaker

`pipeline.VoiceLoop` was removed in favour of `service.VoiceService`. It was never wired to
anything, and its `interrupt()` could not work: the field holding the synthesis task was
only ever assigned `None`, so the cancel was unreachable and barge-in — the one thing the
module's docstring called first-class — silently did nothing.
"""

from thursday_voice.ports import AudioChunk, AudioDevice, Transcript
from thursday_voice.profile import VoiceProfile, profile_for
from thursday_voice.routing import AudioRouter
from thursday_voice.service import AudioSession, VoiceService, VoiceTurn
from thursday_voice.state import VoiceState, VoiceStateError, VoiceStateMachine
from thursday_voice.vad import EnergyVAD, Utterance, UtteranceSegmenter

__all__ = [
    "AudioChunk",
    "AudioDevice",
    "AudioRouter",
    "AudioSession",
    "EnergyVAD",
    "Transcript",
    "Utterance",
    "UtteranceSegmenter",
    "VoiceProfile",
    "VoiceService",
    "VoiceState",
    "VoiceStateError",
    "VoiceStateMachine",
    "VoiceTurn",
    "profile_for",
]
