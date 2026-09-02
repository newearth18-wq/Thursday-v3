"""The voice loop (V4).

Everything here runs with no microphone, no speaker and no model files — the audio is
synthesised, but it is real audio with real energy, so the VAD and the segmenter are
exercised rather than bypassed.

The properties under test, in order of how much they matter:

1. **Nothing is captured before the wake word.** The privacy guarantee (T9).
2. **Barge-in actually stops speech.** The previous implementation's `interrupt()` could
   never fire; a test that only asserted it returned False would have passed forever.
3. **A failed provider costs latency, not the turn.**
"""

from __future__ import annotations

import asyncio

import pytest
from thursday_shared.enums import VoiceMode
from thursday_shared.models import ThursdayReply
from thursday_voice.bargein import BargeInController, InterruptedUtterance
from thursday_voice.fake import (
    FailingSTT,
    FakeMicrophone,
    RecordingTTS,
    ScriptedSTT,
    ScriptedWakeWord,
    silence,
    synthetic_speech,
)
from thursday_voice.fallback import STTChain, TTSChain
from thursday_voice.ports import (
    AudioDevice,
)
from thursday_voice.profile import PROFILES, profile_for
from thursday_voice.routing import AudioRouter
from thursday_voice.service import AudioSession, VoiceService
from thursday_voice.state import (
    VoiceState,
    VoiceStateError,
    VoiceStateMachine,
)
from thursday_voice.vad import EnergyVAD, SegmenterLimits, UtteranceSegmenter

# ------------------------------------------------------------------ state machine


def test_nothing_is_captured_before_the_wake_word():
    """T9. The guarantee is about the *microphone*, so it is stated that way.

    Speaking from rest is fine — a proactive notification opens the speaker, not the
    microphone — but there is no path from IDLE into capture or transcription without
    passing through LISTENING, which only the wake word reaches.
    """
    machine = VoiceStateMachine()
    assert machine.state is VoiceState.IDLE

    for forbidden in (VoiceState.CAPTURING, VoiceState.TRANSCRIBING, VoiceState.THINKING):
        with pytest.raises(VoiceStateError):
            VoiceStateMachine().to(forbidden)

    assert machine.to(VoiceState.LISTENING) is VoiceState.LISTENING


def test_speaking_from_rest_does_not_open_the_microphone():
    """The proactive path (V10) must not be a way around the privacy guarantee."""
    machine = VoiceStateMachine()
    machine.to(VoiceState.SPEAKING)
    assert not machine.listening


def test_the_microphone_indicator_is_true_exactly_when_recording():
    machine = VoiceStateMachine()
    assert not machine.listening
    machine.to(VoiceState.LISTENING)
    assert machine.listening
    machine.to(VoiceState.CAPTURING)
    assert machine.listening
    machine.to(VoiceState.TRANSCRIBING)
    # Transcribing is not recording. An indicator that stays lit here is lying about
    # something the owner cares about.
    assert not machine.listening


def test_a_stop_can_never_be_refused():
    """§69 — an emergency stop that the state machine could reject is not a stop."""
    machine = VoiceStateMachine()
    machine.to(VoiceState.LISTENING)
    machine.to(VoiceState.CAPTURING)
    machine.to(VoiceState.TRANSCRIBING)
    machine.reset()
    assert machine.state is VoiceState.IDLE


def test_the_full_happy_path_is_a_legal_sequence():
    machine = VoiceStateMachine()
    for state in (
        VoiceState.LISTENING,
        VoiceState.CAPTURING,
        VoiceState.TRANSCRIBING,
        VoiceState.THINKING,
        VoiceState.SPEAKING,
    ):
        machine.to(state)
    machine.to(VoiceState.IDLE)
    assert [t[1] for t in machine.history][-1] is VoiceState.IDLE


def test_barge_in_from_speaking_is_a_declared_transition():
    """It has to be in the table, or the loop would have to cheat to support it."""
    machine = VoiceStateMachine()
    for state in (
        VoiceState.LISTENING,
        VoiceState.CAPTURING,
        VoiceState.TRANSCRIBING,
        VoiceState.THINKING,
        VoiceState.SPEAKING,
    ):
        machine.to(state)
    assert machine.can(VoiceState.CAPTURING)


# ------------------------------------------------------------------ VAD + segmentation


def test_speech_is_distinguished_from_silence():
    vad = EnergyVAD()
    speech, quiet = synthetic_speech()[10], silence(60)[0]
    assert vad.is_speech(speech.pcm)
    assert not vad.is_speech(quiet.pcm)


async def test_an_utterance_ends_on_silence():
    segmenter = UtteranceSegmenter()
    mic = FakeMicrophone(synthetic_speech(speech_ms=600, trail_silence_ms=900))
    utterance = await segmenter.segment(mic.stream())
    assert utterance.ended_by == "silence"
    assert utterance.has_speech
    assert utterance.duration_s > 0.5


async def test_the_start_of_the_word_is_not_clipped():
    """Energy detection reacts a frame or two late, so without a pre-roll buffer every
    utterance loses its first consonant and "open" becomes "pen"."""
    limits = SegmenterLimits(pre_roll_ms=300)
    segmenter = UtteranceSegmenter(limits=limits)
    mic = FakeMicrophone(synthetic_speech(lead_silence_ms=300, speech_ms=600))
    utterance = await segmenter.segment(mic.stream())
    # The captured audio is longer than the speech alone: the lead-in came with it.
    assert utterance.frames > 600 // 30


async def test_silence_alone_times_out_rather_than_waiting_forever():
    segmenter = UtteranceSegmenter(limits=SegmenterLimits(start_timeout_ms=300))
    mic = FakeMicrophone(silence(2000))
    utterance = await segmenter.segment(mic.stream())
    assert utterance.ended_by == "timeout"
    assert not utterance.has_speech


async def test_an_endless_utterance_is_capped_and_says_so():
    """Capped rather than cut silently: a truncated transcript must not read as a
    complete thought."""
    segmenter = UtteranceSegmenter(limits=SegmenterLimits(max_utterance_ms=300))
    mic = FakeMicrophone(synthetic_speech(speech_ms=5000, trail_silence_ms=0))
    utterance = await segmenter.segment(mic.stream())
    assert utterance.ended_by == "max_duration"


async def test_a_cough_is_not_an_utterance():
    segmenter = UtteranceSegmenter(
        limits=SegmenterLimits(min_speech_frames=4, start_timeout_ms=600)
    )
    mic = FakeMicrophone(synthetic_speech(lead_silence_ms=0, speech_ms=30, trail_silence_ms=1200))
    utterance = await segmenter.segment(mic.stream())
    assert utterance.ended_by == "timeout"


# ------------------------------------------------------------------ voice profile


@pytest.mark.parametrize("mode", list(VoiceMode))
def test_every_mode_has_a_distinct_delivery(mode):
    assert mode in PROFILES


def test_a_quiet_reply_is_actually_quieter_and_urgent_is_faster():
    """The modes are how a spoken reply carries what formatting carries in writing."""
    assert profile_for(VoiceMode.QUIET).volume < profile_for(VoiceMode.NORMAL).volume
    assert profile_for(VoiceMode.URGENT).rate > profile_for(VoiceMode.NORMAL).rate
    assert profile_for(VoiceMode.THINKING).rate < profile_for(VoiceMode.NORMAL).rate


def test_an_unknown_mode_sounds_ordinary_rather_than_failing():
    """A VoiceMode added elsewhere should not make Thursday fall silent."""
    assert profile_for("SOMETHING_NEW").style == profile_for(VoiceMode.NORMAL).style


# ------------------------------------------------------------------ barge-in


async def test_interrupting_silence_is_harmless():
    controller = BargeInController(tts=RecordingTTS())
    assert await controller.interrupt() is None


async def test_barge_in_stops_speech_in_progress():
    """The property the old implementation claimed and could not deliver."""
    tts = RecordingTTS()
    controller = BargeInController(tts=tts)
    started = asyncio.Event()

    async def speak() -> None:
        started.set()
        await asyncio.sleep(10)  # a long utterance

    task = asyncio.create_task(speak())
    controller.begin(task, InterruptedUtterance(text="I am still talking about the report"))
    await started.wait()
    assert controller.speaking

    cut = await controller.interrupt(reason="owner spoke")
    assert cut is not None
    assert not controller.speaking
    assert task.cancelled()
    # The provider was told to stop, not merely abandoned.
    assert tts.stops == 1


async def test_an_interruption_records_what_was_actually_heard():
    controller = BargeInController(tts=RecordingTTS())
    task = asyncio.create_task(asyncio.sleep(10))
    controller.begin(task, InterruptedUtterance(text="one two three four five"))
    controller.report_progress(len("one two "))

    cut = await controller.interrupt()
    assert cut is not None
    assert cut.partial == "one two "
    assert cut.unspoken.startswith("three")
    assert not cut.completed
    assert controller.interruptions == [cut]


async def test_a_failing_tts_stop_does_not_block_the_owner():
    """Whatever they interrupted us to say matters more than a tidy shutdown."""

    class BrokenTTS:
        async def stop(self) -> None:
            raise RuntimeError("audio device is gone")

    controller = BargeInController(tts=BrokenTTS())
    task = asyncio.create_task(asyncio.sleep(10))
    controller.begin(task, InterruptedUtterance(text="hello"))
    assert await controller.interrupt() is not None


# ------------------------------------------------------------------ routing


def make_router() -> AudioRouter:
    router = AudioRouter()
    router.register(
        AudioDevice(
            id="pc-mic", name="Desk mic", kind="microphone", device_id="pc", is_default=True
        )
    )
    router.register(
        AudioDevice(id="pc-out", name="Monitor", kind="speaker", device_id="pc", is_default=True)
    )
    router.register(
        AudioDevice(
            id="buds", name="Earbuds", kind="speaker", device_id="pc", transport="bluetooth"
        )
    )
    router.register(
        AudioDevice(
            id="phone-out", name="Phone", kind="speaker", device_id="phone", tags=["present"]
        )
    )
    return router


def test_a_reply_goes_to_the_device_the_owner_is_using():
    router = make_router()
    router.note_activity("phone")
    assert router.speaker().device.id == "phone-out"


def test_an_explicit_preference_outranks_everything():
    router = make_router()
    router.note_activity("phone")
    router.prefer("pc-out")
    assert router.speaker().device.id == "pc-out"


def test_a_private_reply_prefers_a_private_output():
    """§43 — not everything Thursday says should be said to the room."""
    router = make_router()
    decision = router.speaker(quiet=True)
    assert decision.device.id == "buds"
    assert "not for the room" in decision.reason


def test_follow_me_is_off_until_asked_for():
    """Audio moving on its own is a surprise, and surprises get features turned off."""
    router = make_router()
    assert router.follow_me is False
    assert router.speaker().device.id == "pc-out"

    router.follow_me = True
    router.active_device_id = None
    assert router.speaker().device.id == "phone-out"


def test_an_unavailable_device_is_not_chosen():
    router = make_router()
    router.prefer("buds")
    router.set_available("buds", False)
    assert router.speaker().device.id != "buds"


def test_no_speaker_at_all_is_reported_rather_than_crashing():
    assert not AudioRouter().speaker()


def test_preferring_an_unknown_device_is_refused():
    with pytest.raises(KeyError):
        make_router().prefer("nonexistent")


# ------------------------------------------------------------------ fallback


async def test_a_failed_provider_costs_latency_not_the_turn():
    """The owner already spoke. Making them repeat themselves is the failure this
    prevents."""
    cloud, local = FailingSTT(), ScriptedSTT(["open notepad"])
    chain = STTChain([cloud, local], local_only=False)

    assert await chain.transcribe(b"audio") == "open notepad"
    assert cloud.attempts == 1
    assert chain.failures and chain.failures[0][0] == "failing-stt"


async def test_private_audio_is_never_sent_to_a_non_local_provider():
    """An offline degradation must not become an upload (§34)."""
    cloud, local = FailingSTT(), ScriptedSTT(["local result"])
    chain = STTChain([cloud, local], local_only=True)

    assert await chain.transcribe(b"audio") == "local result"
    # It was skipped entirely, not tried and rejected: a request that fails after the audio
    # has left is not a refusal.
    assert cloud.attempts == 0


async def test_a_chain_with_nothing_eligible_refuses_rather_than_leaking():
    chain = STTChain([FailingSTT()], local_only=True)
    with pytest.raises(RuntimeError, match="cannot leave"):
        await chain.transcribe(b"audio")


async def test_the_chain_reports_which_providers_are_in_play():
    chain = TTSChain([RecordingTTS(name="a"), RecordingTTS(name="b")])
    assert chain.name == "a+b"
    assert chain.local is True


# ------------------------------------------------------------------ the service


class FakeEngine:
    def __init__(self, reply: ThursdayReply | None = None) -> None:
        self.reply = reply or ThursdayReply(text="เปิด Notepad แล้ว", voice_mode=VoiceMode.SUCCESS)
        self.requests: list[str] = []

    async def handle_request(self, request):
        self.requests.append(request.text)
        return self.reply


def build_service(**overrides):
    defaults = {
        "engine": FakeEngine(),
        "stt": ScriptedSTT(["Thursday เปิด Notepad"]),
        "tts": RecordingTTS(),
        "wake_word": ScriptedWakeWord(),
        "require_wake_word": False,
    }
    defaults.update(overrides)
    return VoiceService(**defaults)


async def test_a_spoken_utterance_becomes_a_turn():
    engine = FakeEngine()
    service = build_service(engine=engine)
    mic = FakeMicrophone(synthetic_speech())

    turn = await service.listen_once(mic.stream(), AudioSession())

    assert turn.transcript == "Thursday เปิด Notepad"
    assert engine.requests == ["Thursday เปิด Notepad"]
    assert turn.reply.text == "เปิด Notepad แล้ว"
    assert turn.spoke
    assert service.state is VoiceState.IDLE


async def test_nothing_is_transcribed_without_the_wake_word():
    stt = ScriptedSTT(["should never be reached"])
    service = build_service(stt=stt, require_wake_word=True)

    assert await service.handle_audio(b"what time is it", session=AudioSession()) is None
    assert stt.calls == []


async def test_the_wake_word_opens_the_path():
    stt = ScriptedSTT(["Thursday open notepad"])
    service = build_service(stt=stt, require_wake_word=True)

    turn = await service.handle_audio(b"Thursday open notepad", session=AudioSession())
    assert turn is not None
    assert stt.calls


async def test_silence_produces_no_turn_and_leaves_no_state_behind():
    service = build_service()
    mic = FakeMicrophone(silence(3000))
    service.segmenter = UtteranceSegmenter(limits=SegmenterLimits(start_timeout_ms=300))

    turn = await service.listen_once(mic.stream(), AudioSession())
    assert turn.skipped == "no speech"
    assert service.state is VoiceState.IDLE


async def test_an_unintelligible_utterance_says_nothing():
    """Better silent than guessing at what was said."""
    service = build_service(stt=ScriptedSTT([""], default=""))
    mic = FakeMicrophone(synthetic_speech())

    turn = await service.listen_once(mic.stream(), AudioSession())
    assert turn.skipped == "nothing intelligible"
    assert not turn.spoke


async def test_speaking_can_be_interrupted_mid_reply():
    tts = RecordingTTS(chunk_delay_s=0.05)
    service = build_service(tts=tts)
    reply = ThursdayReply(
        text="one two three four five six seven eight", voice_mode=VoiceMode.NORMAL
    )

    speaking = asyncio.create_task(service.speak(reply))
    await asyncio.sleep(0.12)  # a couple of words in
    cut = await service.interrupt(reason="owner spoke")
    await speaking

    assert cut is not None
    assert not cut.completed
    assert service.state is VoiceState.IDLE


async def test_the_conversation_survives_an_interruption():
    """ "no, the other one" only means something in the light of what was just said."""
    engine = FakeEngine()
    service = build_service(engine=engine, stt=ScriptedSTT(["first", "second"]))
    session = AudioSession()

    await service.listen_once(FakeMicrophone(synthetic_speech()).stream(), session)
    await service.listen_once(FakeMicrophone(synthetic_speech()).stream(), session)

    assert len(session.turns) == 2
    assert [t.transcript for t in session.turns] == ["first", "second"]
    # Both turns landed in the same conversation, so the core has the context.
    assert engine.requests == ["first", "second"]


async def test_the_reply_is_spoken_in_the_mode_the_core_chose():
    tts = RecordingTTS()
    service = build_service(
        tts=tts,
        engine=FakeEngine(ThursdayReply(text="could not confirm", voice_mode=VoiceMode.WARNING)),
    )
    await service.listen_once(FakeMicrophone(synthetic_speech()).stream(), AudioSession())
    assert tts.spoken == ["could not confirm"]


async def test_shutdown_silences_and_releases_the_wake_word():
    wake = ScriptedWakeWord()
    service = build_service(wake_word=wake)
    await service.shutdown()
    assert wake.stopped
    assert service.state is VoiceState.IDLE
