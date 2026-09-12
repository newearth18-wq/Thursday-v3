"""eSpeak against the real library (V12).

Skipped where `espeakng-loader` is not installed, which is the same condition under which
the container leaves eSpeak out of the TTS chain.

Two tests here exist because of defects found while writing the adapter, and both were found
by *measuring* rather than by reading the documentation — see ADR 0061. They are the reason
this file asserts durations rather than only that bytes came back.
"""

from __future__ import annotations

import pytest
from thursday_shared.audio import is_wav, wav_seconds

espeak = pytest.importorskip("thursday_voice.espeak")
pytestmark = pytest.mark.skipif(not espeak.available(), reason="espeakng-loader not installed")

THAI = "สวัสดีครับ ยินดีต้อนรับสู่งานเปิดบ้านวิชาการ"
ENGLISH = "Everyone is welcome to the school open house"


@pytest.fixture
def tts():
    return espeak.EspeakTTS(voice="th")


async def test_it_speaks_thai_and_returns_a_playable_wav(tts):
    audio = await tts.synthesize(THAI)
    assert is_wav(audio)
    seconds = wav_seconds(audio)
    assert seconds is not None and 1.0 < seconds < 30.0
    assert tts.sample_rate > 0


async def test_it_speaks_english_too(tts):
    audio = await tts.synthesize(ENGLISH, voice="en")
    assert is_wav(audio)
    assert wav_seconds(audio) > 0.5


async def test_the_audio_is_speech_rather_than_silence(tts):
    """A synthesiser that returned the right number of zero samples would pass every
    duration assertion in this file."""
    import array

    audio = await tts.synthesize(THAI)
    samples = array.array("h", audio[44:])
    assert samples, "no samples at all"
    peak = max(abs(s) for s in samples)
    assert peak > 1000, f"peak amplitude {peak} is silence, not speech"


async def test_empty_text_gives_an_empty_wav_rather_than_an_error(tts):
    audio = await tts.synthesize("   ")
    assert is_wav(audio)
    assert wav_seconds(audio) == 0.0


async def test_longer_text_takes_longer_to_say(tts):
    short = wav_seconds(await tts.synthesize("สวัสดี"))
    long = wav_seconds(await tts.synthesize(THAI + " " + THAI))
    assert long > short * 2


async def test_the_same_text_repeated_gives_the_same_duration_every_time(tts):
    """The test that found the output-mode defect, and it needed repetition to find it.

    Under `AUDIO_OUTPUT_RETRIEVAL` eSpeak synthesises on its own thread and audio from one
    utterance lands in the next call's buffer. Two calls usually agree; over twelve, four
    came back at roughly double — 4.07s becoming 8.46s — with nothing raised anywhere.
    Twelve rather than two for exactly that reason.
    """
    measured = [wav_seconds(await tts.synthesize(THAI)) for _ in range(12)]
    assert all(d is not None for d in measured)
    spread = max(measured) - min(measured)
    assert spread < 0.05, f"durations disagree across identical calls: {measured}"


async def test_a_slower_mode_is_actually_slower(tts):
    """Prosody (§6) reaching the engine rather than only the docstring. THINKING is 0.92×
    the rate, so it must take longer than NORMAL."""
    normal = wav_seconds(await tts.synthesize(THAI, mode="NORMAL"))
    thinking = wav_seconds(await tts.synthesize(THAI, mode="THINKING"))
    assert thinking > normal


async def test_rates_stay_ordered_across_every_mode(tts):
    """A faster rate must never take longer. This failed while the adapter used the
    asynchronous output mode, because a duration then depended on what the previous call
    had left behind rather than on the rate."""
    from thursday_core.persona import VOICE_PROFILES
    from thursday_shared.enums import VoiceMode

    measured = {}
    for mode in (VoiceMode.URGENT, VoiceMode.NORMAL, VoiceMode.THINKING):
        measured[mode] = wav_seconds(await tts.synthesize(THAI, mode=mode.value))

    by_rate = sorted(measured, key=lambda m: float(VOICE_PROFILES[m]["rate"]), reverse=True)
    durations = [measured[m] for m in by_rate]
    assert durations == sorted(durations), (
        f"a faster rate must not take longer: {[(m.value, round(measured[m], 2)) for m in by_rate]}"
    )


async def test_the_rate_floor_holds_where_thai_stops_tracking_it(tts):
    """Measured: 100 wpm → 6.90s, 110 → 6.31, 120 → 5.80 — and 90 wpm → 17.46s, two and a
    half times the rate below it. The clamp is why a slow mode cannot reach that."""
    slow = espeak.EspeakTTS(voice="th", base_rate_wpm=40)
    rate, _, _ = slow._settings("THINKING")
    assert rate == espeak.MIN_RATE_WPM

    audio = await slow.synthesize(THAI, mode="THINKING")
    seconds = wav_seconds(audio)
    assert seconds is not None and seconds < 15.0, "the clamp did not hold"


async def test_an_unknown_mode_falls_back_to_normal_rather_than_raising(tts):
    audio = await tts.synthesize(THAI, mode="INTERPRETIVE_DANCE")
    assert wav_seconds(audio) > 0


async def test_streaming_yields_a_chunk_per_sentence(tts):
    chunks = [c async for c in tts.stream_synthesize("หนึ่ง. สอง. สาม.")]
    assert len(chunks) >= 2
    assert all(is_wav(c) for c in chunks)


async def test_stopping_ends_the_stream_between_sentences(tts):
    """Barge-in (§57) needs a seam. The sentence boundary is it."""
    received = []
    async for chunk in tts.stream_synthesize("หนึ่ง. สอง. สาม. สี่. ห้า."):
        received.append(chunk)
        await tts.stop()
    assert len(received) == 1


async def test_it_satisfies_the_tts_port(tts):
    from thursday_shared.interfaces import TTSProvider

    assert isinstance(tts, TTSProvider)
    assert tts.local is True


async def test_concurrent_synthesis_does_not_interleave(tts):
    """eSpeak is one global engine with one global sample buffer. Without the lock, two
    callers in flight together each collect part of the other's audio."""
    import asyncio

    alone = wav_seconds(await tts.synthesize(THAI))
    together = await asyncio.gather(*(tts.synthesize(THAI) for _ in range(6)))
    for audio in together:
        assert wav_seconds(audio) == pytest.approx(alone, abs=0.05)
