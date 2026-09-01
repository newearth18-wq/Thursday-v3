"""Voice loop (§6).

    microphone → wake word → VAD → STT → Thursday Core → TTS → speaker

Barge-in is a first-class state: if the owner speaks while Thursday is speaking, synthesis
stops and the new utterance wins. An assistant you cannot interrupt is an assistant you
end up shouting at.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import VoiceMode
from thursday_shared.models import ThursdayReply

log = get_logger(__name__)


class ListenMode(StrEnum):
    WAKE_WORD = "wake_word"
    PUSH_TO_TALK = "push_to_talk"
    ALWAYS_READY = "always_ready"


@dataclass
class VoiceTurn:
    transcript: str
    reply: ThursdayReply
    audio: bytes


class VoiceLoop:
    def __init__(
        self,
        *,
        engine: object,
        stt: object,
        tts: object,
        wake_word: object,
        mode: ListenMode = ListenMode.WAKE_WORD,
        voice: str = "thursday-neutral",
    ) -> None:
        self._engine = engine
        self._stt = stt
        self._tts = tts
        self._wake = wake_word
        self.mode = mode
        self.voice = voice
        self._speaking: asyncio.Task | None = None
        self.awake = mode is not ListenMode.WAKE_WORD

    async def handle_audio(
        self, audio: bytes, *, session_id: UUID, device_id: UUID | None = None
    ) -> VoiceTurn | None:
        """One captured utterance in, one spoken reply out — or None if not addressed."""
        if self.mode is ListenMode.WAKE_WORD and not self.awake:
            if not await self._wake.detect(audio):
                return None
            self.awake = True
            log.debug("wake_word_detected")

        transcript = await self._stt.transcribe(audio)
        if not transcript.strip():
            return None

        # Barge-in: the owner talking always outranks Thursday talking.
        self.interrupt()

        reply = await self._engine.handle_turn(
            session_id=session_id, text=transcript, device_id=device_id, modality="voice"
        )
        spoken = await self.speak(reply)
        if self.mode is ListenMode.WAKE_WORD:
            self.awake = False
        return VoiceTurn(transcript=transcript, reply=reply, audio=spoken)

    async def speak(self, reply: ThursdayReply) -> bytes:
        mode = (
            reply.voice_mode.value
            if isinstance(reply.voice_mode, VoiceMode)
            else str(reply.voice_mode)
        )
        return await self._tts.synthesize(reply.text, mode=mode, voice=self.voice)

    def interrupt(self) -> bool:
        """§44 — stop the current utterance. Returns True if something was cut short."""
        if self._speaking is not None and not self._speaking.done():
            self._speaking.cancel()
            self._speaking = None
            return True
        return False
