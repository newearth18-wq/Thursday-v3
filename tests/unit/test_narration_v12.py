"""Narration: speaking a script into files, and refusing when it cannot (V12).

The refusals carry most of the weight here. A narrator that quietly wrote 169 bytes of JSON
to `line01.wav` would produce a video with silence where the voice should be, and nothing
anywhere would have failed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from thursday_media.narration import NarratedLine, Narration, Narrator, SpeechSource
from thursday_media.ports import MediaUnavailable, OperationFailed
from thursday_shared.audio import to_wav

RATE = 22_050


class FakeVoice:
    """A synthesiser whose audio is exactly as long as the text is, so a test can assert
    durations without a real engine."""

    name = "fake-voice"
    local = True

    def __init__(self, *, seconds_per_char: float = 0.1) -> None:
        self.seconds_per_char = seconds_per_char
        self.spoken: list[tuple[str, str, str | None]] = []

    async def synthesize(self, text, *, mode="NORMAL", voice=None):
        self.spoken.append((text, mode, voice))
        samples = int(len(text.strip()) * self.seconds_per_char * RATE)
        return to_wav(b"\x00\x00" * samples, RATE)


class DescribesInsteadOfSpeaking:
    """What `TextStubTTS` does: returns the prosody envelope as JSON."""

    name = "text-stub"
    local = True

    async def synthesize(self, text, *, mode="NORMAL", voice=None):
        return json.dumps({"text": text, "mode": mode}).encode()


class Silent:
    name = "silent"
    local = True

    async def synthesize(self, text, *, mode="NORMAL", voice=None):
        return to_wav(b"", RATE)


# ------------------------------------------------------------------------------ speaking


async def test_each_line_becomes_a_file_with_its_measured_length(tmp_path):
    voice = FakeVoice()
    result = await Narrator(voice).narrate(["abcde", "abcdefghij"], tmp_path)

    assert result.durations == pytest.approx([0.5, 1.0], abs=0.01)
    assert result.seconds == pytest.approx(1.5, abs=0.01)
    assert [Path(p).name for p in result.paths] == ["line01.wav", "line02.wav"]
    assert all(Path(line.path).exists() for line in result.lines)
    assert [line.text for line in result.lines] == ["abcde", "abcdefghij"]


async def test_the_backend_and_voice_are_reported(tmp_path):
    result = await Narrator(FakeVoice(), voice="th").narrate(["hello"], tmp_path)
    assert result.backend == "fake-voice"
    assert result.voice == "th"
    assert "fake-voice" in result.describe()


async def test_the_voice_is_passed_through_to_the_synthesiser(tmp_path):
    voice = FakeVoice()
    await Narrator(voice, voice="th").narrate(["ก"], tmp_path)
    assert voice.spoken[0][2] == "th"


async def test_a_per_call_voice_overrides_the_configured_one(tmp_path):
    voice = FakeVoice()
    await Narrator(voice, voice="th").narrate(["x"], tmp_path, voice="en")
    assert voice.spoken[0][2] == "en"


async def test_blank_lines_are_not_spoken(tmp_path):
    voice = FakeVoice()
    result = await Narrator(voice).narrate(["real", "  ", "", "also real"], tmp_path)
    assert len(result.lines) == 2
    assert len(voice.spoken) == 2


async def test_a_custom_prefix_names_the_files(tmp_path):
    result = await Narrator(FakeVoice()).narrate(["a"], tmp_path, prefix="scene")
    assert result.paths[0].endswith("scene01.wav")


async def test_the_working_directory_is_created(tmp_path):
    target = tmp_path / "deep" / "nested"
    result = await Narrator(FakeVoice()).narrate(["a"], target)
    assert target.is_dir() and result.lines


# ------------------------------------------------------------------------------ refusals


async def test_a_backend_that_describes_speech_instead_of_speaking_is_refused(tmp_path):
    """The one that would otherwise fail silently: JSON written to a `.wav`."""
    with pytest.raises(MediaUnavailable) as raised:
        await Narrator(DescribesInsteadOfSpeaking()).narrate(["hello"], tmp_path)

    assert "text-stub" in raised.value.message, "the refusal must name the backend"
    assert "THURSDAY_TTS_BACKEND=espeak" in raised.value.message, "and the remedy"
    assert list(tmp_path.iterdir()) == [], "and must not leave a file behind"


async def test_no_synthesiser_at_all_is_refused_before_anything_is_written(tmp_path):
    narrator = Narrator(None)
    assert not narrator.available
    with pytest.raises(MediaUnavailable, match="no speech synthesiser"):
        await narrator.narrate(["hello"], tmp_path)
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


async def test_silent_audio_is_refused_rather_than_timed_as_zero(tmp_path):
    """A zero-length scene would collapse the video and put every later cue at the wrong
    time, which is worse than stopping."""
    with pytest.raises(OperationFailed, match="unreadable or silent"):
        await Narrator(Silent()).narrate(["hello"], tmp_path)


async def test_an_entirely_empty_script_is_refused(tmp_path):
    with pytest.raises(OperationFailed, match="nothing to narrate"):
        await Narrator(FakeVoice()).narrate(["", "   "], tmp_path)


async def test_a_failure_partway_through_does_not_return_a_partial_narration(tmp_path):
    """A narration missing its third line is not a shorter narration — it is a video whose
    picture and voice stop agreeing at scene three."""

    class FailsOnThird(FakeVoice):
        async def synthesize(self, text, *, mode="NORMAL", voice=None):
            if text == "third":
                return b"not audio at all"
            return await super().synthesize(text, mode=mode, voice=voice)

    with pytest.raises(MediaUnavailable):
        await Narrator(FailsOnThird()).narrate(["first", "second", "third"], tmp_path)


# --------------------------------------------------------------------------------- shape


def test_the_fake_voice_satisfies_the_port():
    assert isinstance(FakeVoice(), SpeechSource)
    assert isinstance(DescribesInsteadOfSpeaking(), SpeechSource)


def test_a_narration_serialises_for_the_task_view():
    narration = Narration(
        lines=[NarratedLine("hello", "/tmp/a.wav", 1.234)], voice="th", backend="espeak-ng"
    )
    payload = narration.to_dict()
    assert payload["backend"] == "espeak-ng"
    assert payload["lines"][0]["seconds"] == 1.234
    assert payload["seconds"] == 1.234
