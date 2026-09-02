"""Voice providers.

The stubs are text-driven: a typed line stands in for a transcript, and synthesis returns
the prosody envelope rather than audio. That keeps the whole voice path — wake, VAD, STT,
mode selection, routing — exercisable in CI with no microphone, no model download and no
audio device, while the real adapters (faster-whisper, Piper, openWakeWord) drop in behind
the same three methods.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable

from thursday_core.persona import VOICE_PROFILES
from thursday_shared.enums import VoiceMode

from thursday_voice.ports import AudioChunk, Transcript

#: Split on sentence ends in both scripts. Thai does not use a full stop between sentences,
#: so a space after a clause is the best available seam — which is all this needs to be:
#: a place to cut synthesis so barge-in can land between words rather than mid-syllable.
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+|(?<=[ๆ])\s+")


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]
    return parts or ([text] if text else [])


class TextStubSTT:
    """Treats the payload as UTF-8 text. Used by the CLI and by tests."""

    name = "text-stub"
    local = True

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return audio.decode("utf-8", errors="replace").strip()

    async def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        buffer = bytearray()
        async for chunk in chunks:
            buffer.extend(chunk.pcm)
        yield Transcript(text=bytes(buffer).decode("utf-8", errors="replace").strip(), final=True)


class WhisperSTT:
    """faster-whisper. Local by design: audio is HIGHLY_PRIVATE by default (§34)."""

    name = "faster-whisper"
    local = True

    def __init__(self, model_size: str = "small", device: str = "auto") -> None:
        self.model_size = model_size
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # imported lazily; heavy

            self._model = WhisperModel(self.model_size, device=self.device, compute_type="int8")
        return self._model

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        import asyncio
        import io

        def run() -> str:
            segments, _ = self._load().transcribe(
                io.BytesIO(audio), language=language, vad_filter=True
            )
            return " ".join(segment.text for segment in segments).strip()

        return await asyncio.to_thread(run)

    async def stream_transcribe(
        self, chunks: AsyncIterator[AudioChunk], *, language: str | None = None
    ) -> AsyncIterator[Transcript]:
        """faster-whisper is not a streaming recogniser, so this buffers and transcribes
        once. Declared anyway rather than omitted: the service should not have to ask which
        providers stream, and a provider that buffers is still correct — only slower to
        first word."""
        buffer = bytearray()
        async for chunk in chunks:
            buffer.extend(chunk.pcm)
        yield Transcript(text=await self.transcribe(bytes(buffer), language=language), final=True)


class TextStubTTS:
    """Returns the prosody envelope as JSON instead of audio.

    A test can then assert *how* Thursday would have spoken — which is the part that carries
    meaning (§6) — without decoding a waveform.
    """

    name = "text-stub"
    local = True

    def __init__(self) -> None:
        self._stopped = False

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        profile = VOICE_PROFILES.get(VoiceMode(mode), VOICE_PROFILES[VoiceMode.NORMAL])
        return json.dumps(
            {"text": text, "mode": mode, "voice": voice or "thursday-neutral", **profile},
            ensure_ascii=False,
        ).encode()

    async def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        """One chunk per sentence, so barge-in has a seam to cut on."""
        self._stopped = False
        for sentence in _sentences(text):
            if self._stopped:
                return
            yield await self.synthesize(sentence, mode=mode, voice=voice)

    async def stop(self) -> None:
        self._stopped = True


class PiperTTS:
    """Piper — local neural TTS, so offline mode still has a voice (§58)."""

    name = "piper"
    local = True

    def __init__(self, model_path: str, *, sample_rate: int = 22050) -> None:
        self.model_path = model_path
        self.sample_rate = sample_rate
        self._process: asyncio.subprocess.Process | None = None
        self._stopped = False

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        import asyncio

        profile = VOICE_PROFILES.get(VoiceMode(mode), VOICE_PROFILES[VoiceMode.NORMAL])
        process = await asyncio.create_subprocess_exec(
            "piper",
            "--model",
            self.model_path,
            "--length_scale",
            str(1 / float(profile["rate"])),
            "--output_file",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._process = process
        try:
            stdout, _ = await process.communicate(text.encode())
        finally:
            self._process = None
        return stdout

    async def stream_synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> AsyncIterator[bytes]:
        """Sentence at a time. Without this, stopping Piper mid-reply would still let the
        whole utterance play, because the audio was already rendered and handed on."""
        self._stopped = False
        for sentence in _sentences(text):
            if self._stopped:
                return
            yield await self.synthesize(sentence, mode=mode, voice=voice)

    async def stop(self) -> None:
        """Kill the renderer. A synthesiser that finishes the sentence after being told to
        stop is not something the owner can interrupt."""
        self._stopped = True
        process = self._process
        if process is not None and process.returncode is None:
            process.kill()


class KeywordWakeWord:
    """Substring wake-word detector over a transcript.

    Real deployments use openWakeWord or Porcupine on raw audio. What matters
    architecturally is the same either way: **nothing is transcribed, stored or sent
    anywhere until the wake word fires** (§9 of the threat model, T9).
    """

    name = "keyword"

    def __init__(self, keyword: str = "thursday") -> None:
        self.keyword = keyword.lower()
        self._on_wake: Callable[[], Awaitable[None] | None] | None = None
        self.listening = False

    async def start(self, on_wake: Callable[[], Awaitable[None] | None]) -> None:
        self._on_wake = on_wake
        self.listening = True

    async def stop(self) -> None:
        self._on_wake = None
        self.listening = False

    async def detect(self, audio: bytes) -> bool:
        heard = self.keyword in audio.decode("utf-8", errors="replace").lower()
        if heard and self._on_wake is not None:
            result = self._on_wake()
            if asyncio.iscoroutine(result):
                await result
        return heard


#: Segmentation lives in `vad.py` now, alongside the utterance logic it belongs with.
#: Re-exported so existing importers do not have to care that it moved.
from thursday_voice.vad import EnergyVAD  # noqa: E402

__all__ = [
    "EnergyVAD",
    "KeywordWakeWord",
    "PiperTTS",
    "TextStubSTT",
    "TextStubTTS",
    "WhisperSTT",
]
