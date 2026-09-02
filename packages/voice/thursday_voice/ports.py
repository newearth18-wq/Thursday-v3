"""The voice ports (V4).

Four things the voice loop needs from the outside world — words in, words out, a wake
signal, and a pair of audio devices. Each is a Protocol, and each has at least a cloud
adapter and a local one (ADR 0001).

Streaming is in the interface rather than bolted on later because it changes the shape of
everything above it. A `transcribe(bytes) -> str` that later grows a streaming sibling ends
up with two code paths through the state machine; declaring both from the start means the
service is written once, against whichever the provider supports.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Transcript:
    """One piece of recognised speech.

    ``final`` distinguishes a stable result from an interim guess. Acting on an interim
    transcript is how an assistant opens the wrong application: "open Chrome" and "open
    Chromium" share a long prefix, and the difference arrives last.
    """

    text: str
    final: bool = True
    confidence: float = 1.0
    language: str | None = None

    def __bool__(self) -> bool:
        return bool(self.text.strip())


@dataclass(frozen=True)
class AudioChunk:
    """A slice of PCM16 audio, with enough context to interpret it."""

    pcm: bytes
    sample_rate: int = 16000
    channels: int = 1

    @property
    def duration_s(self) -> float:
        frames = len(self.pcm) / 2 / max(self.channels, 1)
        return frames / self.sample_rate if self.sample_rate else 0.0


@runtime_checkable
class STTProvider(Protocol):
    """Speech in, text out."""

    name: str
    #: True when transcription happens on this machine. Audio is HIGHLY_PRIVATE by
    #: default (§34), so the router needs to know before it sends anything anywhere.
    local: bool

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """One complete utterance."""
        ...

    def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        """Interim results as the owner speaks, ending with a final one."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text in, audio out."""

    name: str
    local: bool

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes: ...

    def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        """Audio in pieces, so speech starts before the whole sentence is rendered.

        This is what makes barge-in meaningful: a provider that returns one finished blob
        can be discarded, but the owner has already heard all of it.
        """
        ...

    async def stop(self) -> None:
        """Abandon the utterance in progress. Must be safe to call when not speaking."""
        ...


@runtime_checkable
class WakeWordProvider(Protocol):
    """Listens for the name, and for nothing else.

    Nothing is transcribed, stored or sent anywhere until this fires (T9). That is the
    entire reason it is a separate component from the STT provider: a design where the
    recogniser runs continuously and the wake word is a filter afterwards has already sent
    the audio.
    """

    name: str
    keyword: str

    async def start(self, on_wake: Callable[[], Awaitable[None] | None]) -> None: ...

    async def stop(self) -> None: ...

    async def detect(self, audio: bytes) -> bool:
        """Single-shot check, for push-to-talk and for tests."""
        ...


@runtime_checkable
class AudioSource(Protocol):
    """A microphone, or something standing in for one."""

    name: str

    def stream(self) -> AsyncIterator[AudioChunk]: ...

    async def close(self) -> None: ...


@runtime_checkable
class AudioSink(Protocol):
    """A speaker, or something standing in for one."""

    name: str

    async def play(self, audio: bytes) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class AudioDevice:
    """One physical or logical endpoint the router can choose between."""

    id: str
    name: str
    kind: str  # "microphone" | "speaker"
    #: Which Thursday device node this belongs to. None means the machine running the core.
    device_id: str | None = None
    transport: str = "local"  # local | bluetooth | phone | network
    is_default: bool = False
    available: bool = True
    tags: list[str] = field(default_factory=list)

    def describe(self) -> str:
        where = f" on {self.device_id}" if self.device_id else ""
        return f"{self.name} ({self.transport}{where})"
