"""Test doubles for the voice path (V4, PART 88).

Shipped rather than confined to the test suite, for the same reason ``FakeDeviceNode`` is:
CI has no microphone, the desktop app needs a way to demo the loop without one, and anyone
extending the voice providers needs devices they can make misbehave on demand.

``synthetic_speech`` is the important one. Energy-based VAD needs audio that actually has
energy in the right places, so a test that feeds it zeros proves nothing about segmentation.
This produces a waveform with real speech and real silence, which means the segmenter is
exercised rather than bypassed.
"""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from thursday_voice.ports import AudioChunk, Transcript

SAMPLE_RATE = 16000
FRAME_MS = 30


def _frame(amplitude: float, *, ms: int = FRAME_MS, sample_rate: int = SAMPLE_RATE) -> bytes:
    """One frame of a 220 Hz tone at the given amplitude. Silence when amplitude is 0."""
    count = int(sample_rate * ms / 1000)
    samples = [
        int(amplitude * 32767 * math.sin(2 * math.pi * 220 * n / sample_rate)) for n in range(count)
    ]
    return struct.pack(f"<{count}h", *samples)


def synthetic_speech(
    *, lead_silence_ms: int = 150, speech_ms: int = 900, trail_silence_ms: int = 900
) -> list[AudioChunk]:
    """Silence, then speech, then silence — the shape of one spoken sentence."""
    frames: list[AudioChunk] = []
    for ms, amplitude in (
        (lead_silence_ms, 0.0),
        (speech_ms, 0.35),
        (trail_silence_ms, 0.0),
    ):
        for _ in range(max(0, ms // FRAME_MS)):
            frames.append(AudioChunk(pcm=_frame(amplitude), sample_rate=SAMPLE_RATE))
    return frames


def silence(ms: int) -> list[AudioChunk]:
    return [AudioChunk(pcm=_frame(0.0), sample_rate=SAMPLE_RATE) for _ in range(ms // FRAME_MS)]


class FakeMicrophone:
    """An audio source that replays a scripted list of chunks."""

    name = "fake-microphone"

    def __init__(
        self, chunks: list[AudioChunk] | None = None, *, loop_silence: bool = True
    ) -> None:
        self.chunks = list(chunks or [])
        #: After the script runs out, keep emitting silence rather than ending the stream.
        #: A real microphone does not stop existing because nobody is talking, and a
        #: segmenter tested only against streams that end cleanly never meets a timeout.
        self.loop_silence = loop_silence
        self.closed = False
        self.emitted = 0

    def feed(self, chunks: list[AudioChunk]) -> None:
        self.chunks.extend(chunks)

    async def stream(self) -> AsyncIterator[AudioChunk]:
        for chunk in self.chunks:
            if self.closed:
                return
            self.emitted += 1
            yield chunk
            await asyncio.sleep(0)
        while self.loop_silence and not self.closed:
            self.emitted += 1
            yield AudioChunk(pcm=_frame(0.0), sample_rate=SAMPLE_RATE)
            await asyncio.sleep(0)

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeSpeaker:
    """An audio sink that records what it was asked to play."""

    name: str = "fake-speaker"
    played: list[bytes] = field(default_factory=list)
    stops: int = 0
    #: Seconds per play() call, for exercising barge-in against speech in progress.
    play_duration_s: float = 0.0

    async def play(self, audio: bytes) -> None:
        if self.play_duration_s:
            await asyncio.sleep(self.play_duration_s)
        self.played.append(audio)

    async def stop(self) -> None:
        self.stops += 1

    @property
    def spoken_bytes(self) -> int:
        return sum(len(p) for p in self.played)


class ScriptedSTT:
    """Returns whatever it was told to return, ignoring the audio.

    The audio is real and the transcript is scripted on purpose: it keeps the segmenter and
    the routing honest while leaving the test in control of what was "said".
    """

    name = "scripted-stt"
    local = True

    def __init__(self, transcripts: list[str] | None = None, *, default: str = "") -> None:
        self.transcripts = list(transcripts or [])
        self.default = default
        self.calls: list[int] = []

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        self.calls.append(len(audio))
        return self.transcripts.pop(0) if self.transcripts else self.default

    async def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        total = 0
        async for chunk in chunks:
            total += len(chunk.pcm)
        text = self.transcripts.pop(0) if self.transcripts else self.default
        self.calls.append(total)
        # Interim first, then final — so a consumer that acts on interim results is caught.
        if text:
            yield Transcript(text=text[: max(1, len(text) // 2)], final=False, confidence=0.5)
        yield Transcript(text=text, final=True)


class FailingSTT:
    """An STT provider that always fails. For exercising the fallback chain."""

    name = "failing-stt"
    local = False

    def __init__(self, error: str = "the speech service is unreachable") -> None:
        self.error = error
        self.attempts = 0

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        self.attempts += 1
        raise ConnectionError(self.error)

    async def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        self.attempts += 1
        raise ConnectionError(self.error)
        yield Transcript(text="")  # pragma: no cover - unreachable, satisfies the generator


@dataclass
class RecordingTTS:
    """Synthesis that yields in pieces, so barge-in has something to interrupt."""

    name: str = "recording-tts"
    local: bool = True
    chunk_delay_s: float = 0.0
    spoken: list[str] = field(default_factory=list)
    stops: int = 0

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        self.spoken.append(text)
        return f"[{mode}] {text}".encode()

    async def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        self.spoken.append(text)
        for word in text.split():
            if self.chunk_delay_s:
                await asyncio.sleep(self.chunk_delay_s)
            yield f"{word} ".encode()

    async def stop(self) -> None:
        self.stops += 1


class ScriptedWakeWord:
    """Fires when told to, and reports what it heard.

    ``detect`` matches the keyword in the decoded payload, so a text-driven test can wake
    Thursday exactly as the shipped ``KeywordWakeWord`` does.
    """

    name = "scripted-wake"

    def __init__(self, keyword: str = "thursday") -> None:
        self.keyword = keyword.lower()
        self.started = False
        self.stopped = False
        self._on_wake = None
        self.wakes = 0

    async def start(self, on_wake) -> None:
        self.started = True
        self._on_wake = on_wake

    async def stop(self) -> None:
        self.stopped = True
        self._on_wake = None

    async def detect(self, audio: bytes) -> bool:
        return self.keyword in audio.decode("utf-8", errors="replace").lower()

    async def fire(self) -> None:
        """Simulate the wake word being heard."""
        self.wakes += 1
        if self._on_wake is not None:
            result = self._on_wake()
            if asyncio.iscoroutine(result):
                await result
