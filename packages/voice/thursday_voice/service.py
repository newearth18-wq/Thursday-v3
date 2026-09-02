"""VoiceService — the realtime voice loop (V4).

    wake word → microphone → VAD → STT → Thursday Core → verification → TTS → speaker

One object owns the state machine, the audio session and the barge-in controller, because
those three are the same concern seen from three angles: *is the microphone open, and who
is allowed to be talking right now.* Splitting them across the codebase is how a voice
assistant ends up recording while it thinks it is idle.

Everything below the ports is replaceable (ADR 0001), so this same service runs against a
real microphone, a fake one, or a WebSocket carrying audio from a phone.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import VoiceMode
from thursday_shared.ids import new_id
from thursday_shared.models import Event, ThursdayReply, UserRequest

from thursday_voice.bargein import BargeInController, InterruptedUtterance
from thursday_voice.ports import AudioChunk
from thursday_voice.profile import profile_for
from thursday_voice.routing import AudioRouter
from thursday_voice.state import VoiceState, VoiceStateMachine
from thursday_voice.vad import Utterance, UtteranceSegmenter

log = get_logger(__name__)


@dataclass
class VoiceTurn:
    """One complete exchange, from spoken words to spoken reply."""

    transcript: str
    reply: ThursdayReply | None = None
    audio: bytes = b""
    utterance: Utterance | None = None
    interrupted: InterruptedUtterance | None = None
    #: Why nothing happened, when nothing happened. Set for a turn that was not addressed
    #: to Thursday, could not be understood, or was cut short.
    skipped: str | None = None

    @property
    def spoke(self) -> bool:
        return bool(self.audio)


@dataclass
class AudioSession:
    """One conversation's audio state.

    Separate from the service because a session is per-conversation and per-device, while
    the service is per-process. Keeping the conversation id here is what makes barge-in
    able to preserve context: the follow-up utterance lands in the same conversation as the
    thing it was correcting.
    """

    conversation_id: UUID = field(default_factory=new_id)
    device_id: UUID | None = None
    #: The audio endpoint the owner is speaking from, for routing the reply back.
    audio_device_id: str | None = None
    language: str | None = None
    turns: list[VoiceTurn] = field(default_factory=list)

    def record(self, turn: VoiceTurn) -> VoiceTurn:
        self.turns.append(turn)
        return turn


class VoiceService:
    """The loop. Owns state, and refuses to be in two of them at once."""

    def __init__(
        self,
        *,
        engine: Any,
        stt: Any,
        tts: Any,
        wake_word: Any,
        router: AudioRouter | None = None,
        segmenter: UtteranceSegmenter | None = None,
        bus: Any = None,
        voice: str = "thursday-neutral",
        require_wake_word: bool = True,
    ) -> None:
        self._engine = engine
        self._stt = stt
        self._tts = tts
        self._wake = wake_word
        self.router = router or AudioRouter()
        self.segmenter = segmenter or UtteranceSegmenter()
        self._bus = bus
        self.voice = voice
        self.require_wake_word = require_wake_word

        self.machine = VoiceStateMachine(on_change=self._on_state_change)
        self.bargein = BargeInController(tts=tts)
        self._state_events: list[tuple[VoiceState, VoiceState]] = []
        #: Strong references to in-flight publishes; without these the garbage
        #: collector can cancel a task mid-flight and the event silently vanishes.
        self._pending: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> VoiceState:
        return self.machine.state

    @property
    def listening(self) -> bool:
        """Drives the recording indicator. Must be true exactly when audio is captured."""
        return self.machine.listening

    def _on_state_change(self, previous: VoiceState, current: VoiceState) -> None:
        self._state_events.append((previous, current))
        log.debug("voice_state", **{"from": str(previous), "to": str(current)})
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Called from synchronous code (a test, or startup). The state is still
            # recorded above; only the notification is skipped.
            return
        # Fire and forget. A subscriber must never be able to stall the microphone, so the
        # publish is not awaited and its failure is the bus's problem, not the loop's.
        task = loop.create_task(
            self._bus.publish(
                Event(kind="voice.state", payload={"from": str(previous), "to": str(current)})
            )
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # ------------------------------------------------------------------ the loop

    async def wake(self) -> bool:
        """Leave IDLE. Returns False if already awake."""
        if self.machine.state is not VoiceState.IDLE:
            return False
        self.machine.to(VoiceState.LISTENING)
        return True

    async def listen_once(
        self,
        chunks: AsyncIterator[AudioChunk],
        session: AudioSession,
        *,
        already_awake: bool = False,
    ) -> VoiceTurn:
        """Capture one utterance, run it through the core, and speak the reply."""
        if not already_awake and self.machine.state is VoiceState.IDLE:
            await self.wake()

        # If Thursday is mid-sentence, the owner speaking outranks it (§44).
        interrupted = None
        if self.bargein.speaking:
            interrupted = await self.bargein.interrupt(reason="owner spoke")

        if self.machine.state is VoiceState.SPEAKING or self.machine.state is VoiceState.LISTENING:
            self.machine.to(VoiceState.CAPTURING)

        utterance = await self.segmenter.segment(chunks)
        if not utterance.has_speech:
            self.machine.reset()
            return session.record(
                VoiceTurn(transcript="", utterance=utterance, skipped="no speech")
            )

        self.machine.to(VoiceState.TRANSCRIBING)
        transcript = (await self._stt.transcribe(utterance.pcm, language=session.language)).strip()
        if not transcript:
            self.machine.reset()
            return session.record(
                VoiceTurn(transcript="", utterance=utterance, skipped="nothing intelligible")
            )

        log.info("voice_transcript", chars=len(transcript), ended_by=utterance.ended_by)

        self.machine.to(VoiceState.THINKING)
        response = await self._engine.handle_request(
            UserRequest(
                conversation_id=session.conversation_id,
                text=transcript,
                device_id=session.device_id,
                modality="voice",
            )
        )
        reply = _as_reply(response)

        audio = await self.speak(reply, session=session)
        self.machine.reset()
        return session.record(
            VoiceTurn(
                transcript=transcript,
                reply=reply,
                audio=audio,
                utterance=utterance,
                interrupted=interrupted,
            )
        )

    async def handle_audio(
        self, audio: bytes, *, session: AudioSession, awake: bool = False
    ) -> VoiceTurn | None:
        """One captured utterance as bytes — the push-to-talk and WebSocket path.

        Returns None when the audio was not addressed to Thursday, which is different from
        a turn that produced no reply: one means "not for me", the other means "I heard you
        and had nothing to say".
        """
        if self.require_wake_word and not awake:
            if not await self._wake.detect(audio):
                return None
            log.debug("wake_word_detected")

        if self.machine.state is VoiceState.IDLE:
            self.machine.to(VoiceState.LISTENING)
        if self.bargein.speaking:
            await self.bargein.interrupt(reason="owner spoke")
        if self.machine.state in (VoiceState.LISTENING, VoiceState.SPEAKING):
            self.machine.to(VoiceState.CAPTURING)

        self.machine.to(VoiceState.TRANSCRIBING)
        transcript = (await self._stt.transcribe(audio, language=session.language)).strip()
        if not transcript:
            self.machine.reset()
            return session.record(VoiceTurn(transcript="", skipped="nothing intelligible"))

        self.machine.to(VoiceState.THINKING)
        response = await self._engine.handle_request(
            UserRequest(
                conversation_id=session.conversation_id,
                text=transcript,
                device_id=session.device_id,
                modality="voice",
            )
        )
        reply = _as_reply(response)
        spoken = await self.speak(reply, session=session)
        self.machine.reset()
        return session.record(VoiceTurn(transcript=transcript, reply=reply, audio=spoken))

    # ------------------------------------------------------------------ speaking

    async def speak(self, reply: ThursdayReply, *, session: AudioSession | None = None) -> bytes:
        """Say a reply, interruptibly.

        Synthesis runs as a task the barge-in controller owns, which is the whole mechanism:
        an utterance nobody holds a handle to is an utterance nobody can stop.
        """
        if self.machine.state is not VoiceState.SPEAKING:
            self.machine.to(VoiceState.SPEAKING)

        mode = reply.voice_mode if isinstance(reply.voice_mode, VoiceMode) else VoiceMode.NORMAL
        profile = profile_for(mode, language=session.language if session else None)
        sink = self._sink_for(reply, session)

        collected: list[bytes] = []
        utterance = InterruptedUtterance(text=reply.text, mode=str(mode))

        async def run() -> None:
            spoken_chars = 0
            stream = getattr(self._tts, "stream_synthesize", None)
            if stream is not None:
                async for piece in stream(reply.text, mode=str(mode), voice=self.voice):
                    collected.append(piece)
                    if sink is not None:
                        await sink.play(piece)
                    # Report what has actually reached the speaker, not what was queued.
                    spoken_chars += len(piece.decode("utf-8", errors="ignore"))
                    self.bargein.report_progress(spoken_chars)
            else:
                audio = await self._tts.synthesize(reply.text, mode=str(mode), voice=self.voice)
                collected.append(audio)
                if sink is not None:
                    await sink.play(audio)
                self.bargein.report_progress(len(reply.text))

        task = asyncio.create_task(run(), name="voice:speak")
        self.bargein.begin(task, utterance)
        try:
            await task
        except asyncio.CancelledError:
            # Interrupted. Whatever was rendered before the cut is still returned, so the
            # transcript shows what the owner actually heard.
            return b"".join(collected)
        await self.bargein.finished()
        log.debug("voice_spoke", chars=len(reply.text), mode=str(mode), profile=profile.style)
        return b"".join(collected)

    def _sink_for(self, reply: ThursdayReply, session: AudioSession | None) -> Any:
        quiet = reply.voice_mode is VoiceMode.QUIET
        decision = self.router.speaker(
            device_id=session.audio_device_id if session else None, quiet=quiet
        )
        if not decision:
            return None
        return getattr(decision.device, "sink", None)

    # ------------------------------------------------------------------ control

    async def interrupt(self, *, reason: str = "stop requested") -> InterruptedUtterance | None:
        """Stop speaking now. The path behind "Thursday หยุด" (§69)."""
        cut = await self.bargein.interrupt(reason=reason)
        self.machine.reset()
        return cut

    async def shutdown(self) -> None:
        await self.bargein.interrupt(reason="session ended")
        stop = getattr(self._wake, "stop", None)
        if stop is not None:
            await stop()
        self.machine.reset()

    def snapshot(self) -> dict:
        return {
            "state": str(self.machine.state),
            "listening": self.machine.listening,
            "speaking": self.bargein.speaking,
            "stt": getattr(self._stt, "name", "?"),
            "tts": getattr(self._tts, "name", "?"),
            "wake_word": getattr(self._wake, "keyword", "?"),
            "audio": self.router.snapshot(),
        }


def _as_reply(response: Any) -> ThursdayReply:
    """Accept either a ThursdayResponse or a ThursdayReply."""
    if isinstance(response, ThursdayReply):
        return response
    return ThursdayReply(
        text=getattr(response, "text", str(response)),
        voice_mode=getattr(response, "voice_mode", VoiceMode.NORMAL),
        verified=getattr(response, "verified", True),
        confidence=getattr(response, "confidence", 1.0),
        task_id=getattr(response, "task_id", None),
    )
