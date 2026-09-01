"""Voice providers.

The stubs are text-driven: a typed line stands in for a transcript, and synthesis returns
the prosody envelope rather than audio. That keeps the whole voice path — wake, VAD, STT,
mode selection, routing — exercisable in CI with no microphone, no model download and no
audio device, while the real adapters (faster-whisper, Piper, openWakeWord) drop in behind
the same three methods.
"""

from __future__ import annotations

import json
import math
import struct

from thursday.core.persona import VOICE_PROFILES
from thursday.shared.enums import VoiceMode


class TextStubSTT:
    """Treats the payload as UTF-8 text. Used by the CLI and by tests."""

    name = "text-stub"
    local = True

    async def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        return audio.decode("utf-8", errors="replace").strip()


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


class TextStubTTS:
    """Returns the prosody envelope as JSON instead of audio.

    A test can then assert *how* Thursday would have spoken — which is the part that carries
    meaning (§6) — without decoding a waveform.
    """

    name = "text-stub"
    local = True

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes:
        profile = VOICE_PROFILES.get(VoiceMode(mode), VOICE_PROFILES[VoiceMode.NORMAL])
        return json.dumps(
            {"text": text, "mode": mode, "voice": voice or "thursday-neutral", **profile},
            ensure_ascii=False,
        ).encode()


class PiperTTS:
    """Piper — local neural TTS, so offline mode still has a voice (§58)."""

    name = "piper"
    local = True

    def __init__(self, model_path: str, *, sample_rate: int = 22050) -> None:
        self.model_path = model_path
        self.sample_rate = sample_rate

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
        stdout, _ = await process.communicate(text.encode())
        return stdout


class KeywordWakeWord:
    """Substring wake-word detector over a transcript.

    Real deployments use openWakeWord or Porcupine on raw audio. What matters
    architecturally is the same either way: **nothing is transcribed, stored or sent
    anywhere until the wake word fires** (§9 of the threat model, T9).
    """

    def __init__(self, keyword: str = "thursday") -> None:
        self.keyword = keyword.lower()

    async def detect(self, audio: bytes) -> bool:
        return self.keyword in audio.decode("utf-8", errors="replace").lower()


class EnergyVAD:
    """Voice activity detection by frame energy — enough to segment push-to-talk audio."""

    def __init__(
        self, *, threshold: float = 0.02, frame_ms: int = 30, sample_rate: int = 16000
    ) -> None:
        self.threshold = threshold
        self.frame_ms = frame_ms
        self.sample_rate = sample_rate

    def is_speech(self, pcm16: bytes) -> bool:
        if not pcm16:
            return False
        count = len(pcm16) // 2
        samples = struct.unpack(f"<{count}h", pcm16[: count * 2])
        rms = math.sqrt(sum(s * s for s in samples) / count) / 32768
        return rms >= self.threshold
