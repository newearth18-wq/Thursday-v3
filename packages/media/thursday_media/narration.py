"""Script lines into spoken audio, with the durations that come back (§15, V12).

This is the piece that closes the gap V11 left open. `creative.compose` could always time
subtitles two ways — measured against narration somebody supplied, or estimated from reading
speed — and until now nothing in Thursday could *produce* the narration, so every video it
assembled took the estimated path. A synthesiser behind the `TTSProvider` port fills that in:
speak each line, read how long the audio actually is, and hand those durations to the
composer, which then places every cue on the frame the voice starts.

**It refuses rather than degrading.** The offline `TextStubTTS` returns a JSON description of
how Thursday *would* have spoken — exactly right for a test asserting prosody, and not
something that can be laid under a video. A narrator holding one says so, by name, before it
writes anything: the alternative is a folder of files containing `{"text": ...}` with a `.wav`
extension, and a video with silence where the narration should be.

Nothing here imports `thursday_voice`. The port is structural, so any object with
`synthesize` satisfies it, and the WAV reading lives in `thursday_shared.audio` — which is
also why narration works on a machine with no ffmpeg: a duration comes out of the file's own
header.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from thursday_core.logging import get_logger
from thursday_shared.audio import is_wav, wav_seconds

from thursday_media.ports import MediaUnavailable, OperationFailed

log = get_logger(__name__)


@runtime_checkable
class SpeechSource(Protocol):
    """The half of `TTSProvider` narration needs. Structural, so nothing has to import it."""

    name: str

    async def synthesize(
        self, text: str, *, mode: str = "NORMAL", voice: str | None = None
    ) -> bytes: ...


@dataclass(frozen=True)
class NarratedLine:
    """One spoken line: what was said, where it landed, and how long it actually runs."""

    text: str
    path: str
    seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "path": self.path, "seconds": round(self.seconds, 3)}


@dataclass
class Narration:
    lines: list[NarratedLine] = field(default_factory=list)
    voice: str = ""
    backend: str = ""

    @property
    def seconds(self) -> float:
        return sum(line.seconds for line in self.lines)

    @property
    def paths(self) -> list[str]:
        return [line.path for line in self.lines]

    @property
    def durations(self) -> list[float]:
        return [line.seconds for line in self.lines]

    def describe(self) -> str:
        return f"{len(self.lines)} lines, {self.seconds:.1f}s spoken by {self.backend}" + (
            f" ({self.voice})" if self.voice else ""
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "voice": self.voice,
            "seconds": round(self.seconds, 3),
            "lines": [line.to_dict() for line in self.lines],
        }


class Narrator:
    """Speaks a script into a folder of WAV files."""

    def __init__(self, tts: Any = None, *, voice: str = "", mode: str = "NORMAL") -> None:
        self._tts = tts
        self.voice = voice
        self.mode = mode

    @property
    def backend(self) -> str:
        return str(getattr(self._tts, "name", "none")) if self._tts is not None else "none"

    @property
    def available(self) -> bool:
        """Whether there is a synthesiser at all.

        Deliberately **not** whether it produces audio: that is only knowable by asking it to
        speak, and claiming to know it in advance would be the same guess this module exists
        to avoid. The audio check happens in `narrate`, against real bytes.
        """
        return self._tts is not None and hasattr(self._tts, "synthesize")

    async def narrate(
        self, lines: list[str], workdir: Path, *, prefix: str = "line", voice: str = ""
    ) -> Narration:
        """Speak each line into its own file and measure it.

        Raises rather than returning a partial result. A narration missing its third line is
        not a shorter narration — it is a video whose picture and voice stop agreeing at
        scene three, and every cue after it is wrong.
        """
        if not self.available:
            raise MediaUnavailable(
                "There is no speech synthesiser configured, so Thursday cannot narrate "
                "this. Set THURSDAY_TTS_BACKEND=espeak (pip install espeakng-loader) and "
                "restart. Nothing was written."
            )

        spoken = [line for line in lines if line.strip()]
        if not spoken:
            raise OperationFailed("there is nothing to narrate — every line is empty")

        workdir = Path(workdir)
        await asyncio.to_thread(workdir.mkdir, parents=True, exist_ok=True)

        narration = Narration(voice=voice or self.voice, backend=self.backend)
        for index, text in enumerate(spoken, start=1):
            audio = await self._tts.synthesize(
                text, mode=self.mode, voice=(voice or self.voice) or None
            )

            if not is_wav(audio):
                raise MediaUnavailable(
                    f"The configured speech backend ({self.backend}) does not return audio "
                    f"— it returned {len(audio)} bytes that are not a WAV. The offline "
                    "text-stub voice describes how Thursday would speak rather than "
                    "speaking, which cannot be laid under a video. Set "
                    "THURSDAY_TTS_BACKEND=espeak. Nothing was written."
                )

            seconds = wav_seconds(audio)
            if seconds is None or seconds <= 0:
                raise OperationFailed(
                    f"line {index} came back as {len(audio)} bytes of unreadable or silent "
                    "audio, so the scene it narrates cannot be timed"
                )

            path = workdir / f"{prefix}{index:02d}.wav"
            await asyncio.to_thread(path.write_bytes, audio)
            narration.lines.append(NarratedLine(text=text, path=str(path), seconds=seconds))

        log.info(
            "narrated",
            lines=len(narration.lines),
            seconds=round(narration.seconds, 2),
            backend=narration.backend,
        )
        return narration
