"""Voice activity detection and utterance segmentation (V4).

Classifying one frame as speech is the easy half. The half that matters is deciding when an
utterance *ended*, and that decision is what makes a voice assistant feel usable or not.

Cut too early and you truncate someone mid-sentence. Cut too late and every exchange has an
awkward pause in it. Neither is fixable further up the stack: the transcript is already
wrong by then. So the segmenter here is explicit about all three of its bounds — how much
silence ends an utterance, how long one may run, and how long to wait for speech that never
comes — and none of them is unbounded.
"""

from __future__ import annotations

import math
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass

from thursday_core.logging import get_logger

from thursday_voice.ports import AudioChunk

log = get_logger(__name__)


class EnergyVAD:
    """Frame energy against a threshold.

    Crude next to a neural VAD, and entirely adequate for deciding whether someone is
    talking into a close microphone. It runs locally with no model, which matters: audio is
    HIGHLY_PRIVATE (§34), and the component deciding whether to *keep* audio must not be
    the component that ships it somewhere.
    """

    name = "energy"

    def __init__(
        self, *, threshold: float = 0.02, frame_ms: int = 30, sample_rate: int = 16000
    ) -> None:
        self.threshold = threshold
        self.frame_ms = frame_ms
        self.sample_rate = sample_rate

    def rms(self, pcm16: bytes) -> float:
        if not pcm16:
            return 0.0
        count = len(pcm16) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", pcm16[: count * 2])
        return math.sqrt(sum(s * s for s in samples) / count) / 32768

    def is_speech(self, pcm16: bytes) -> bool:
        return self.rms(pcm16) >= self.threshold


@dataclass
class Utterance:
    """One segmented stretch of speech, and why it ended."""

    pcm: bytes
    sample_rate: int = 16000
    duration_s: float = 0.0
    #: "silence" — the owner stopped talking (the normal case).
    #: "max_duration" — the cap was hit; the audio is real but may be cut off.
    #: "timeout" — nothing was ever said.
    ended_by: str = "silence"
    frames: int = 0

    @property
    def has_speech(self) -> bool:
        return self.ended_by != "timeout" and bool(self.pcm)


@dataclass
class SegmenterLimits:
    """Every bound in one place, so none of them is implicit.

    ``pre_roll_ms`` is the least obvious and the most noticeable: energy detection reacts a
    frame or two after speech actually starts, so without a small backward buffer every
    utterance loses its first consonant, and "open" becomes "pen".
    """

    silence_ms: int = 700
    max_utterance_ms: int = 15_000
    start_timeout_ms: int = 6_000
    pre_roll_ms: int = 300
    #: Frames of speech needed before capture begins, so a cough is not an utterance.
    min_speech_frames: int = 2


class UtteranceSegmenter:
    """Turns a stream of audio chunks into complete utterances."""

    def __init__(self, vad: EnergyVAD | None = None, limits: SegmenterLimits | None = None) -> None:
        self.vad = vad or EnergyVAD()
        self.limits = limits or SegmenterLimits()

    async def segment(self, chunks: AsyncIterator[AudioChunk]) -> Utterance:
        """Collect one utterance. Returns as soon as it ends, for any of the three reasons."""
        limits = self.limits
        frame_ms = self.vad.frame_ms
        silence_needed = max(1, limits.silence_ms // frame_ms)
        max_frames = max(1, limits.max_utterance_ms // frame_ms)
        start_deadline = max(1, limits.start_timeout_ms // frame_ms)
        pre_roll_frames = max(0, limits.pre_roll_ms // frame_ms)

        pre_roll: list[bytes] = []
        collected: list[bytes] = []
        speech_frames = 0
        silence_run = 0
        elapsed = 0
        started = False
        sample_rate = self.vad.sample_rate

        async for chunk in chunks:
            sample_rate = chunk.sample_rate
            elapsed += 1
            speaking = self.vad.is_speech(chunk.pcm)

            if not started:
                # Keep a short backward buffer so the start of the word survives.
                pre_roll.append(chunk.pcm)
                if len(pre_roll) > pre_roll_frames:
                    pre_roll.pop(0)

                speech_frames = speech_frames + 1 if speaking else 0
                if speech_frames >= limits.min_speech_frames:
                    started = True
                    collected.extend(pre_roll)
                    silence_run = 0
                elif elapsed >= start_deadline:
                    log.debug("utterance_start_timeout", waited_frames=elapsed)
                    return Utterance(pcm=b"", sample_rate=sample_rate, ended_by="timeout")
                continue

            collected.append(chunk.pcm)
            silence_run = 0 if speaking else silence_run + 1

            if silence_run >= silence_needed:
                return self._finish(collected, sample_rate, frame_ms, "silence")
            if len(collected) >= max_frames:
                # Capped rather than cut silently: the caller is told the audio may be
                # incomplete, so a truncated transcript is not read as a complete thought.
                log.info("utterance_hit_max_duration", ms=limits.max_utterance_ms)
                return self._finish(collected, sample_rate, frame_ms, "max_duration")

        # The stream ended — the microphone closed, or the session was torn down.
        if not started:
            return Utterance(pcm=b"", sample_rate=sample_rate, ended_by="timeout")
        return self._finish(collected, sample_rate, frame_ms, "silence")

    def _finish(
        self, frames: list[bytes], sample_rate: int, frame_ms: int, reason: str
    ) -> Utterance:
        pcm = b"".join(frames)
        return Utterance(
            pcm=pcm,
            sample_rate=sample_rate,
            duration_s=len(frames) * frame_ms / 1000,
            ended_by=reason,
            frames=len(frames),
        )
