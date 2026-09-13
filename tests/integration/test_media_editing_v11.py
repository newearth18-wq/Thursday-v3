"""The editing operations against a real ffmpeg (V11).

Skipped when there is no ffmpeg on the machine, which is the same condition under which the
container builds an `UnavailableEditor` — so a run with no ffmpeg still proves the refusal
path in `tests/unit/test_media_ffmpeg_v11.py`, and this file proves the working one.

Every assertion here is made against a **probe of the output file**, never against an exit
code. That is rule 1 of the README applied to rendering: ffmpeg exits zero in situations
where it wrote nothing useful, so "it worked" has to mean "the file contains what was asked
for".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_media.ffmpeg import FFmpegEditor, discover
from thursday_media.ports import OperationFailed, Overlay, preset
from thursday_media.quality import check_output
from thursday_media.subtitles import from_lines, to_srt

from tests.fonts import thai_font_available

FFMPEG, _ = discover()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="no ffmpeg on this machine")

#: Sprint 107. These burn Thai text into a picture, and on a machine whose fonts cannot draw
#: it they used to pass while producing a video of empty boxes — ffmpeg exits 0 and boxes are
#: pixels (ADR 0081). Burn-in now refuses, so the honest answer here is a skip, exactly as a
#: machine with no ffmpeg skips. CI installs a Thai font and asserts it, so CI never skips.
needs_thai_font = pytest.mark.skipif(
    not thai_font_available(), reason="no font on this machine can draw Thai"
)

pytestmark = [needs_ffmpeg]


@pytest.fixture
def editor(tmp_path: Path) -> FFmpegEditor:
    return FFmpegEditor.discovered(allowed_roots=(tmp_path,))


async def _make(editor: FFmpegEditor, args: list[str]) -> None:
    """Build source material with the same binary under test.

    Using ffmpeg's own synthetic sources rather than committing binary fixtures: a repo with
    sample videos in it is a repo nobody can review, and `testsrc` is deterministic.
    """
    process = await asyncio.create_subprocess_exec(
        editor.ffmpeg,
        "-y",
        "-v",
        "error",
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()


async def clip(
    editor: FFmpegEditor,
    path: Path,
    *,
    seconds: float = 4.0,
    size: str = "640x360",
    audio: bool = True,
    hz: int = 440,
) -> Path:
    args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    args += (["-c:a", "aac", "-shortest"] if audio else ["-an"]) + [str(path)]
    await _make(editor, args)
    return path


async def image(
    editor: FFmpegEditor, path: Path, *, colour: str = "navy", size: str = "800x600"
) -> Path:
    await _make(
        editor, ["-f", "lavfi", "-i", f"color=c={colour}:size={size}", "-frames:v", "1", str(path)]
    )
    return path


async def tone(editor: FFmpegEditor, path: Path, *, seconds: float = 3.0, hz: int = 300) -> Path:
    await _make(
        editor,
        ["-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}", "-c:a", "aac", str(path)],
    )
    return path


# ------------------------------------------------------------------------------- capability


async def test_the_editor_reports_what_this_build_can_actually_do(editor):
    """Asked of the binary rather than assumed. A distribution build without libx264 cannot
    produce H.264, and finding that out at the end of a render is the worst moment."""
    health = await editor.probe_capabilities()
    assert health.available
    assert health.version
    assert {c.name for c in health.capabilities} >= {"h264", "aac", "subtitles"}


# ------------------------------------------------------------------------------- operations


async def test_probing_reads_a_real_file(editor, tmp_path):
    source = await clip(editor, tmp_path / "a.mp4", seconds=3.0)
    probe = await editor.probe(str(source))
    assert probe.seconds == pytest.approx(3.0, abs=0.15)
    assert probe.has_video and probe.has_audio
    assert (probe.video.width, probe.video.height) == (640, 360)
    assert probe.size_bytes > 1024


async def test_a_trim_produces_exactly_the_section_asked_for(editor, tmp_path):
    """Re-encoded rather than stream-copied: a keyframe cut on 30fps H.264 can land two
    seconds from where the owner asked."""
    source = await clip(editor, tmp_path / "a.mp4", seconds=6.0)
    out = await editor.trim(str(source), str(tmp_path / "cut.mp4"), start=1.0, end=3.0)
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(2.0, abs=0.2)


async def test_a_still_becomes_a_clip_of_the_right_length_and_shape(editor, tmp_path):
    card = await image(editor, tmp_path / "card.png")
    out = await editor.still(
        str(card), str(tmp_path / "card.mp4"), seconds=2.5, target=preset("9:16")
    )
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(2.5, abs=0.2)
    assert (probe.video.width, probe.video.height) == (1080, 1920)
    assert not probe.has_audio, "a still carries no audio by design"


async def test_concatenation_adds_the_lengths_up(editor, tmp_path):
    first = await clip(editor, tmp_path / "a.mp4", seconds=2.0)
    second = await clip(editor, tmp_path / "b.mp4", seconds=3.0)
    out = await editor.concat([str(first), str(second)], str(tmp_path / "joined.mp4"))
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(5.0, abs=0.3)
    assert probe.has_audio


async def test_a_silent_clip_joined_to_a_noisy_one_keeps_the_picture_in_sync(editor, tmp_path):
    """The bug this guards: dropping a silent input out of the audio graph shortens the
    soundtrack, and every clip after it plays against the wrong picture."""
    silent = await clip(editor, tmp_path / "silent.mp4", seconds=3.0, audio=False)
    noisy = await clip(editor, tmp_path / "noisy.mp4", seconds=2.0)
    out = await editor.concat([str(silent), str(noisy)], str(tmp_path / "joined.mp4"))
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(5.0, abs=0.3)
    assert probe.has_audio, "silence should be supplied, not skipped"


async def test_clips_of_different_sizes_are_normalised_before_joining(editor, tmp_path):
    """Real material never shares a frame size — a phone clip, a screen recording and a
    still are three different shapes, and the concat filter requires one."""
    big = await clip(editor, tmp_path / "big.mp4", seconds=2.0, size="1280x720")
    small = await clip(editor, tmp_path / "small.mp4", seconds=2.0, size="320x240")
    out = await editor.concat([str(big), str(small)], str(tmp_path / "joined.mp4"))
    probe = await editor.probe(out)
    assert (probe.video.width, probe.video.height) == (1280, 720)
    assert probe.seconds == pytest.approx(4.0, abs=0.3)


async def test_resizing_letterboxes_rather_than_cropping(editor, tmp_path):
    """Scale-then-pad, because a crop that fills 9:16 from a 16:9 photo removes a third of
    the picture — reliably including somebody's head."""
    source = await clip(editor, tmp_path / "a.mp4", seconds=2.0, size="1280x720")
    out = await editor.resize(str(source), str(tmp_path / "vertical.mp4"), target=preset("tiktok"))
    probe = await editor.probe(out)
    assert (probe.video.width, probe.video.height) == (1080, 1920)
    assert probe.seconds == pytest.approx(2.0, abs=0.2)


@needs_thai_font
async def test_burning_subtitles_actually_draws_pixels(editor, tmp_path):
    """Comparing a frame before and after. libass silently renders nothing when it cannot
    find the file, and the output is then a perfectly valid video with no subtitles in it —
    a pass that a duration check would not catch."""
    source = await clip(editor, tmp_path / "flat.mp4", seconds=4.0)
    track = from_lines(["สวัสดีครับ ยินดีต้อนรับ", "Welcome to the school event"])
    srt = tmp_path / "subs.srt"
    srt.write_text(to_srt(track), encoding="utf-8")

    out = await editor.burn_subtitles(str(source), str(tmp_path / "subbed.mp4"), subtitles=str(srt))
    plain_frame = await editor.thumbnail(str(source), str(tmp_path / "plain.png"), at=1.0)
    subbed_frame = await editor.thumbnail(out, str(tmp_path / "subbed.png"), at=1.0)

    assert Path(subbed_frame).read_bytes() != Path(plain_frame).read_bytes()
    assert Path(subbed_frame).stat().st_size > Path(plain_frame).stat().st_size


async def test_a_subtitle_path_with_shell_and_filter_characters_is_handled(editor, tmp_path):
    """`:`, `'` and `\\` all have meaning in ffmpeg's filter syntax. The file is staged
    under a plain name rather than escaped, which removes the question."""
    source = await clip(editor, tmp_path / "a.mp4", seconds=2.0)
    awkward = tmp_path / "it's 19:30; run.srt"
    awkward.write_text(to_srt(from_lines(["hello"], durations=[1.0])), encoding="utf-8")
    out = await editor.burn_subtitles(
        str(source), str(tmp_path / "out.mp4"), subtitles=str(awkward)
    )
    assert (await editor.probe(out)).has_video


async def test_dubbing_keeps_the_video_length_when_the_music_is_shorter(editor, tmp_path):
    """`-shortest` would have produced a three-second video under an eight-second picture.
    The output is cut with an explicit `-t` instead."""
    source = await clip(editor, tmp_path / "long.mp4", seconds=8.0, audio=False)
    music = await tone(editor, tmp_path / "music.m4a", seconds=3.0)
    out = await editor.dub(str(source), str(tmp_path / "dubbed.mp4"), music=str(music))
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(8.0, abs=0.3)
    assert probe.has_audio


async def test_narration_and_music_are_mixed_together(editor, tmp_path):
    source = await clip(editor, tmp_path / "v.mp4", seconds=5.0, audio=False)
    narration = await tone(editor, tmp_path / "narr.m4a", seconds=4.0, hz=300)
    music = await tone(editor, tmp_path / "music.m4a", seconds=2.0, hz=150)
    out = await editor.dub(
        str(source),
        str(tmp_path / "mixed.mp4"),
        narration=str(narration),
        music=str(music),
        music_gain_db=-20.0,
    )
    probe = await editor.probe(out)
    assert probe.has_audio
    assert probe.seconds == pytest.approx(5.0, abs=0.3)


async def test_loudness_normalisation_keeps_the_picture(editor, tmp_path):
    source = await clip(editor, tmp_path / "a.mp4", seconds=3.0)
    out = await editor.normalize_audio(str(source), str(tmp_path / "norm.mp4"))
    probe = await editor.probe(out)
    assert probe.has_video and probe.has_audio


async def test_normalising_a_file_with_no_audio_is_refused(editor, tmp_path):
    silent = await clip(editor, tmp_path / "silent.mp4", seconds=2.0, audio=False)
    with pytest.raises(OperationFailed, match="no audio"):
        await editor.normalize_audio(str(silent), str(tmp_path / "out.mp4"))


async def test_silence_is_removed_from_narration(editor, tmp_path):
    """One second of tone, two of silence. What comes back should be about a second."""
    narration = tmp_path / "narr.m4a"
    await _make(
        editor,
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=1,apad=pad_dur=2",
            "-t",
            "3",
            "-c:a",
            "aac",
            str(narration),
        ],
    )
    assert (await editor.probe(str(narration))).seconds == pytest.approx(3.0, abs=0.2)

    out = await editor.strip_silence(str(narration), str(tmp_path / "tight.m4a"))
    tightened = (await editor.probe(out)).seconds
    assert tightened is not None and tightened < 2.0


async def test_stripping_silence_from_a_video_is_refused_rather_than_desynced(editor, tmp_path):
    """The honest half of the brief's 'silence removal'. Cutting the soundtrack while
    leaving the picture alone puts them out of sync for the rest of the video."""
    source = await clip(editor, tmp_path / "v.mp4", seconds=3.0)
    with pytest.raises(OperationFailed, match="out of sync"):
        await editor.strip_silence(str(source), str(tmp_path / "out.mp4"))


async def test_an_overlay_changes_the_picture_where_it_is_placed(editor, tmp_path):
    source = await clip(editor, tmp_path / "v.mp4", seconds=4.0)
    logo = await image(editor, tmp_path / "logo.png", colour="red", size="120x60")
    out = await editor.overlay(
        str(source),
        str(tmp_path / "over.mp4"),
        overlays=[Overlay(image=str(logo), corner="bottom-right")],
    )
    before = await editor.thumbnail(str(source), str(tmp_path / "before.png"), at=1.0)
    after = await editor.thumbnail(out, str(tmp_path / "after.png"), at=1.0)
    assert Path(after).read_bytes() != Path(before).read_bytes()


async def test_an_unknown_corner_is_refused(editor, tmp_path):
    source = await clip(editor, tmp_path / "v.mp4", seconds=2.0)
    logo = await image(editor, tmp_path / "logo.png", size="60x60")
    with pytest.raises(OperationFailed, match="is not a corner"):
        await editor.overlay(
            str(source),
            str(tmp_path / "o.mp4"),
            overlays=[Overlay(image=str(logo), corner="middle-ish")],
        )


async def test_a_crossfade_overlaps_the_two_clips(editor, tmp_path):
    """Five seconds plus three, dissolving for one, is seven — not eight."""
    first = await clip(editor, tmp_path / "a.mp4", seconds=5.0)
    second = await clip(editor, tmp_path / "b.mp4", seconds=3.0, hz=880)
    out = await editor.crossfade(str(first), str(second), str(tmp_path / "xf.mp4"), seconds=1.0)
    probe = await editor.probe(out)
    assert probe.seconds == pytest.approx(7.0, abs=0.3)


async def test_a_crossfade_longer_than_the_first_clip_is_refused(editor, tmp_path):
    first = await clip(editor, tmp_path / "a.mp4", seconds=1.0)
    second = await clip(editor, tmp_path / "b.mp4", seconds=3.0)
    with pytest.raises(OperationFailed, match="cannot hold"):
        await editor.crossfade(str(first), str(second), str(tmp_path / "xf.mp4"), seconds=2.0)


async def test_a_thumbnail_past_the_end_of_the_video_is_refused(editor, tmp_path):
    """ffmpeg exits zero and writes nothing. An exit code is not evidence."""
    source = await clip(editor, tmp_path / "a.mp4", seconds=2.0)
    with pytest.raises(OperationFailed, match="shorter"):
        await editor.thumbnail(str(source), str(tmp_path / "late.png"), at=30.0)


async def test_a_garbled_input_fails_with_ffmpeg_s_own_reason(editor, tmp_path):
    """Not 'error 1'. The brief asks for messages somebody can act on."""
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is definitely not an mp4" * 200)
    with pytest.raises(OperationFailed) as raised:
        await editor.trim(str(broken), str(tmp_path / "out.mp4"), start=0.0, end=1.0)
    assert "failed:" in raised.value.message
    assert len(raised.value.message) > 20


async def test_the_quality_gate_passes_a_real_render(editor, tmp_path):
    source = await clip(editor, tmp_path / "a.mp4", seconds=4.0)
    out = await editor.resize(str(source), str(tmp_path / "square.mp4"), target=preset("1:1"))
    report = check_output(
        await editor.probe(out), target=preset("1:1"), expect_audio=True, min_seconds=3.0
    )
    assert report.ok, report.describe()
    assert report.certain


async def test_the_quality_gate_catches_a_render_in_the_wrong_shape(editor, tmp_path):
    source = await clip(editor, tmp_path / "a.mp4", seconds=2.0)
    out = await editor.resize(str(source), str(tmp_path / "wide.mp4"), target=preset("16:9"))
    report = check_output(await editor.probe(out), target=preset("9:16"))
    assert not report.ok
    assert any(c.name == "dimensions" for c in report.failures)


async def test_dubbing_onto_something_with_no_picture_is_refused_clearly(editor, tmp_path):
    """ffmpeg's own failure here is about stream specifiers and says nothing about what to
    do instead. The brief asks for messages somebody can act on."""
    narration = await tone(editor, tmp_path / "a.m4a", seconds=2.0)
    music = await tone(editor, tmp_path / "b.m4a", seconds=2.0, hz=200)
    with pytest.raises(OperationFailed, match="no picture to dub onto"):
        await editor.dub(str(narration), str(tmp_path / "out.mp4"), music=str(music))


@needs_thai_font
async def test_subtitles_burn_onto_a_silent_video_too(editor, tmp_path):
    """The `-c:a copy` branch has to disappear when there is no audio to copy."""
    source = await clip(editor, tmp_path / "silent.mp4", seconds=3.0, audio=False)
    srt = tmp_path / "s.srt"
    srt.write_text(to_srt(from_lines(["เงียบ"], durations=[2.0])), encoding="utf-8")
    out = await editor.burn_subtitles(str(source), str(tmp_path / "out.mp4"), subtitles=str(srt))
    probe = await editor.probe(out)
    assert probe.has_video and not probe.has_audio
