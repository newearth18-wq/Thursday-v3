"""V12 acceptance: Thursday speaks the script, and the subtitles land on the voice (§15).

V11 could assemble a video from narration somebody supplied, and could not produce any —
so every video it made took the estimated-timing path. This is the other half: a script
goes in, Thursday speaks it, and each scene becomes exactly as long as the line spoken over
it.

The assertions are made against the **finished file and the real audio**, never against a
return value. Skipped without eSpeak or without ffmpeg, which are the same conditions under
which Thursday says it cannot do this here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_media.creative import PromoRequest, Scene, compose
from thursday_media.ffmpeg import FFmpegEditor, discover
from thursday_media.narration import Narrator
from thursday_media.plan import render
from thursday_media.quality import check_output
from thursday_shared.audio import wav_seconds

espeak = pytest.importorskip("thursday_voice.espeak")
FFMPEG, _ = discover()
pytestmark = [
    pytest.mark.skipif(not FFMPEG, reason="no ffmpeg on this machine"),
    pytest.mark.skipif(not espeak.available(), reason="espeakng-loader not installed"),
]

SCRIPT = [
    "พรุ่งนี้โรงเรียนของเรามีงานเปิดบ้านวิชาการ",
    "มีนิทรรศการจากทุกกลุ่มสาระการเรียนรู้",
    "Everyone is welcome, the hall opens at nine",
]


@pytest.fixture
def editor(tmp_path: Path) -> FFmpegEditor:
    return FFmpegEditor.discovered(allowed_roots=(tmp_path,))


@pytest.fixture
def narrator():
    return Narrator(espeak.EspeakTTS(voice="th"), voice="th")


async def _card(editor: FFmpegEditor, path: Path, colour: str) -> str:
    process = await asyncio.create_subprocess_exec(
        editor.ffmpeg,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={colour}:size=1280x720",
        "-frames:v",
        "1",
        str(path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await process.communicate()
    assert process.returncode == 0, err.decode()
    return str(path)


async def test_thursday_speaks_a_script_into_a_finished_subtitled_video(editor, narrator, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    scenes = [
        Scene(text=line, image=await _card(editor, work / f"c{i}.png", c))
        for i, (line, c) in enumerate(zip(SCRIPT, ("navy", "darkgreen", "maroon"), strict=True))
    ]
    request = PromoRequest(
        name="openhouse",
        scenes=scenes,
        workdir=work,
        aspect="9:16",
        narrate=True,
        voice="th",
    )

    build = await compose(request, editor, narrator)
    assert build.ready, build.describe()
    assert build.subtitles.measured, "a script Thursday spoke is measured, not estimated"
    assert build.narration is not None
    assert len(build.narration.lines) == 3

    # Each scene is exactly as long as its own narration file, read off the real audio.
    for line, cue in zip(build.narration.lines, build.subtitles.cues, strict=True):
        spoken = wav_seconds(Path(line.path).read_bytes())
        assert spoken == pytest.approx(line.seconds, abs=0.001)
        assert cue.seconds == pytest.approx(spoken, abs=0.001)

    report = await render(build.plan, editor)
    assert report.ok, report.describe()

    probe = await editor.probe(report.deliverable)
    assert (probe.video.width, probe.video.height) == (1080, 1920)
    assert probe.has_audio, "the narration has to be in the file"
    assert probe.seconds == pytest.approx(build.narration.seconds, abs=1.0)

    quality = check_output(
        probe, target=request.target, expect_audio=True, subtitles=build.subtitles
    )
    assert quality.ok, quality.describe()
    assert quality.certain
    subtitle_check = next(c for c in quality.checks if c.name == "subtitles")
    assert "synced" in subtitle_check.detail, "these cues were measured against real speech"


async def test_the_narration_in_the_file_is_audible_rather_than_silence(editor, narrator, tmp_path):
    """A render that laid down a silent track would pass every duration check above."""
    import array

    work = tmp_path / "work"
    work.mkdir()
    scenes = [
        Scene(text=SCRIPT[0], image=await _card(editor, work / "a.png", "navy")),
        Scene(text=SCRIPT[1], image=await _card(editor, work / "b.png", "olive")),
    ]
    build = await compose(
        PromoRequest(name="loud", scenes=scenes, workdir=work, narrate=True, voice="th"),
        editor,
        narrator,
    )
    report = await render(build.plan, editor)
    assert report.ok, report.describe()

    # Pull the soundtrack back out of the finished video and look at it.
    extracted = work / "check.wav"
    process = await asyncio.create_subprocess_exec(
        editor.ffmpeg,
        "-y",
        "-v",
        "error",
        "-i",
        report.deliverable,
        "-vn",
        "-ac",
        "1",
        "-f",
        "wav",
        str(extracted),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await process.communicate()
    assert process.returncode == 0, err.decode()

    samples = array.array("h", extracted.read_bytes()[44:])
    peak = max(abs(s) for s in samples)
    assert peak > 1000, f"the finished video's audio peaks at {peak} — that is silence"


async def test_without_a_voice_the_request_is_refused_rather_than_guessed(editor, tmp_path):
    """`narrate=True` with nothing to narrate with never quietly falls back to reading
    speed: that would answer a request for a spoken video with a silent one."""
    work = tmp_path / "work"
    work.mkdir()
    build = await compose(
        PromoRequest(
            name="mute",
            scenes=[Scene(text=SCRIPT[0], image=await _card(editor, work / "a.png", "navy"))],
            workdir=work,
            narrate=True,
        ),
        editor,
        None,
    )
    assert not build.ready
    assert not build.plan.steps
    assert any("speech synthesiser" in m for m in build.missing)


async def test_the_same_script_spoken_twice_produces_the_same_timings(narrator, tmp_path):
    """Synthesis is deterministic, and a re-run of a plan must not shift its subtitles."""
    first = await narrator.narrate(SCRIPT, tmp_path / "one")
    second = await narrator.narrate(SCRIPT, tmp_path / "two")
    assert first.durations == pytest.approx(second.durations, abs=0.001)
