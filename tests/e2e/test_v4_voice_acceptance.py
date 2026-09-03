"""V4 acceptance test — the spoken vertical slice.

    "Thursday เปิด Notepad"
      → wake word → microphone → VAD → STT → Thursday Core
      → Computer Agent → permission → device node → Notepad opens
      → node verifies the process exists → Supervisor → task COMPLETED
      → TTS → "เปิด Notepad แล้ว"

Everything below the audio ports is the real system: the real core, the real permission
engine, the real node executor with its path jail and its ACT→VERIFY loop. Only the
microphone and the speaker are imaginary, and the audio flowing through them has real
energy so the VAD and segmenter do their actual job.

The negative case at the end is the one that matters, exactly as it does for text: when
Notepad does not start, the spoken reply must not say it did.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from thursday_devices.fake import FakeDeviceNode
from thursday_shared.enums import TaskState, VoiceMode
from thursday_voice.fake import (
    FakeMicrophone,
    RecordingTTS,
    ScriptedSTT,
    ScriptedWakeWord,
    synthetic_speech,
)
from thursday_voice.service import AudioSession, VoiceService
from thursday_voice.state import VoiceState


@pytest.fixture
async def spoken(container, tmp_path: Path):
    """A voice service on the real container, with a fake machine at the far end."""
    node = FakeDeviceNode(name="Office-PC", allowed_roots=[tmp_path])
    session = node.session()
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Office-PC")

    def service(transcript: str, *, tts: RecordingTTS | None = None) -> VoiceService:
        return VoiceService(
            engine=container.engine,
            stt=ScriptedSTT([transcript]),
            tts=tts or RecordingTTS(),
            wake_word=ScriptedWakeWord(),
            require_wake_word=False,
        )

    return node, session, service


async def test_thursday_opens_notepad_when_spoken_to(spoken, container):
    node, device, make_service = spoken
    tts = RecordingTTS()
    service = make_service("Thursday เปิด Notepad", tts=tts)
    audio = AudioSession(device_id=device.device_id)

    turn = await service.listen_once(FakeMicrophone(synthetic_speech()).stream(), audio)

    # 1-3. Audio was captured, segmented and transcribed.
    assert turn.utterance is not None and turn.utterance.has_speech
    assert turn.utterance.ended_by == "silence"
    assert turn.transcript == "Thursday เปิด Notepad"

    # 4-6. The core understood it, the agent acted, the machine changed.
    assert "notepad" in node.adapter.running

    # 7. The node verified by observation, not by the absence of an exception.
    task = container.tasks.list()[0]
    assert task.verification is not None and task.verification.passed
    assert task.status is TaskState.COMPLETED

    # 8. Thursday said so, out loud, in the success mode.
    assert turn.reply is not None
    assert turn.reply.verified is True
    assert turn.reply.voice_mode is VoiceMode.SUCCESS
    assert "notepad" in turn.reply.text.lower()
    assert tts.spoken == [turn.reply.text]
    assert turn.spoke

    # 9. And the loop is back at rest with the microphone closed.
    assert service.state is VoiceState.IDLE
    assert not service.listening


async def test_the_spoken_reply_does_not_claim_success_it_cannot_verify(container, tmp_path):
    """The negative case. The launch command succeeds, no process appears — and the owner
    must not *hear* that it worked."""
    from tests.helpers import connect_failing_node

    device = await connect_failing_node(container, tmp_path)
    tts = RecordingTTS()
    service = VoiceService(
        engine=container.engine,
        stt=ScriptedSTT(["Thursday open notepad"]),
        tts=tts,
        wake_word=ScriptedWakeWord(),
        require_wake_word=False,
    )

    turn = await service.listen_once(
        FakeMicrophone(synthetic_speech()).stream(), AudioSession(device_id=device.device_id)
    )

    assert container.tasks.list()[0].status is TaskState.FAILED
    assert turn.reply is not None
    assert turn.reply.verified is False
    assert turn.reply.voice_mode is not VoiceMode.SUCCESS
    # It still spoke — silence would be its own failure — but not a claim of success.
    assert turn.spoke
    assert "เรียบร้อย" not in turn.reply.text


async def test_the_wake_word_gates_the_whole_path(container, tmp_path):
    """T9 end to end: without the name, nothing is transcribed and nothing happens."""
    node = FakeDeviceNode(name="Office-PC", allowed_roots=[tmp_path])
    device = node.session()
    await container.hub.register(device)
    container.world.update(active_device_id=device.device_id, active_device_name="Office-PC")

    stt = ScriptedSTT(["เปิด Notepad"])
    service = VoiceService(
        engine=container.engine,
        stt=stt,
        tts=RecordingTTS(),
        wake_word=ScriptedWakeWord(),
        require_wake_word=True,
    )

    unaddressed = await service.handle_audio(
        b"open notepad please", session=AudioSession(device_id=device.device_id)
    )
    assert unaddressed is None
    assert stt.calls == []
    assert "notepad" not in node.adapter.running
    assert container.tasks.list() == []

    addressed = await service.handle_audio(
        b"Thursday open notepad", session=AudioSession(device_id=device.device_id)
    )
    assert addressed is not None
    assert stt.calls


async def test_stop_spoken_aloud_halts_the_reply(spoken, container):
    """§69 — "Thursday หยุด" has to work while Thursday is the one talking."""
    import asyncio

    _node, _device, make_service = spoken
    tts = RecordingTTS(chunk_delay_s=0.05)
    service = make_service("Thursday เปิด Notepad", tts=tts)

    from thursday_shared.models import ThursdayReply

    long_reply = ThursdayReply(text="one two three four five six seven eight nine ten")
    speaking = asyncio.create_task(service.speak(long_reply))
    await asyncio.sleep(0.12)
    cut = await service.interrupt(reason="Thursday หยุด")
    await speaking

    assert cut is not None
    assert not cut.completed
    assert tts.stops >= 1
    assert service.state is VoiceState.IDLE
