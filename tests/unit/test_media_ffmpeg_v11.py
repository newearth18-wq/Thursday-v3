"""The ffmpeg adapter's parsing and its guard rails, with no ffmpeg involved (V11).

The operations themselves need a real binary and live in
`tests/integration/test_media_editing_v11.py`. What is here is everything that can be
checked without one: the probe parser against captured output, the path jail, and the rule
that an edit never writes over its input.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from thursday_media.ffmpeg import FFmpegEditor, discover, parse_probe
from thursday_media.ports import MediaEditor, OperationFailed
from thursday_media.unavailable import UnavailableEditor

#: Captured from ffmpeg 7.0.2 rather than invented, so the parser is tested against the
#: shape the binary actually emits.
VIDEO_WITH_AUDIO = """
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/tmp/a.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:00:03.00, start: 0.000000, bitrate: 138 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 640x360 [SAR 1:1 DAR 16:9], 56 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 69 kb/s (default)
"""

STILL_IMAGE = """
Input #0, png_pipe, from '/tmp/img.png':
  Duration: N/A, bitrate: N/A
  Stream #0:0: Video: png, rgb24(pc, gbr/unknown/unknown), 800x600 [SAR 1:1 DAR 4:3], 25 fps, 25 tbr, 25 tbn
"""

STEREO_AUDIO = """
Input #0, wav, from '/tmp/n.wav':
  Duration: 00:01:05.50, bitrate: 1411 kb/s
  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 48000 Hz, stereo, s16, 1536 kb/s
"""


def test_the_parser_reads_duration_streams_and_dimensions():
    probe = parse_probe(VIDEO_WITH_AUDIO, path="/tmp/a.mp4", size_bytes=52_071)
    assert probe.seconds == pytest.approx(3.0)
    assert probe.container == "mov"
    assert probe.has_video and probe.has_audio
    assert (probe.video.width, probe.video.height) == (640, 360)
    assert probe.video.codec == "h264"
    assert probe.video.fps == pytest.approx(30.0)
    assert probe.audio.channels == 1
    assert probe.audio.sample_rate == 44100
    assert probe.describe() == "640×360, h264, 3.0s, with audio"


def test_a_duration_of_n_a_is_none_and_not_zero():
    """A still has no duration. Reporting that as zero seconds would make the quality
    checker fail an image for being empty."""
    probe = parse_probe(STILL_IMAGE)
    assert probe.seconds is None
    assert probe.has_video
    assert not probe.has_audio
    assert probe.video.width == 800


def test_stereo_and_a_long_duration_parse():
    probe = parse_probe(STEREO_AUDIO)
    assert probe.seconds == pytest.approx(65.5)
    assert probe.audio.channels == 2
    assert not probe.has_video
    assert "silent" not in probe.describe()


def test_unreadable_output_gives_an_empty_probe_rather_than_raising():
    probe = parse_probe("ffmpeg: not a media file at all")
    assert probe.streams == ()
    assert probe.seconds is None
    assert probe.describe() == "unreadable"


def test_a_frame_size_is_not_mistaken_for_a_bitrate():
    """`_SIZE` once matched the `0x31637661` in a codec tag and reported a 31637661-pixel
    frame."""
    probe = parse_probe(VIDEO_WITH_AUDIO)
    assert probe.video.width == 640 and probe.video.height == 360


# ------------------------------------------------------------------------------ guard rails


def test_a_missing_ffmpeg_is_a_state_rather_than_an_error():
    """Discovery returns empty strings; the refusal happens at the point of use, with a
    remedy. That is what lets the UI show 'not installed' instead of a traceback."""
    editor = FFmpegEditor(ffmpeg="", ffprobe="")
    assert not editor.available
    health = editor.health()
    assert health["available"] is False
    assert "not installed" in str(health["detail"])


def test_discovery_ignores_a_configured_path_that_is_not_a_file(tmp_path):
    found, _ = discover(str(tmp_path / "nope" / "ffmpeg"))
    # Falls through to the PATH / bundled wheel rather than returning the bad path.
    assert found != str(tmp_path / "nope" / "ffmpeg")


async def test_an_edit_refuses_to_write_over_its_own_input(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="also an input"):
        await editor.trim(str(clip), str(clip), start=0.0, end=1.0)


async def test_paths_outside_the_allowed_roots_are_refused(tmp_path):
    """The same path jail the device node uses. A plan can name a file, and a plan can come
    from a model."""
    inside = tmp_path / "work"
    inside.mkdir()
    clip = inside / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    editor = FFmpegEditor(ffmpeg="/usr/bin/true", allowed_roots=(inside,))

    with pytest.raises(OperationFailed, match="outside the folders"):
        await editor.trim("/etc/passwd", str(inside / "out.mp4"), start=0.0)
    with pytest.raises(OperationFailed, match="outside the folders"):
        await editor.trim(str(clip), "/tmp/escape.mp4", start=0.0)


async def test_a_missing_input_says_so_before_ffmpeg_is_started(tmp_path):
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="no file at"):
        await editor.trim(str(tmp_path / "ghost.mp4"), str(tmp_path / "out.mp4"), start=0.0)


async def test_an_empty_input_is_refused(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.touch()
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="is empty"):
        await editor.trim(str(empty), str(tmp_path / "out.mp4"), start=0.0)


async def test_nonsense_clip_boundaries_are_refused(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="cannot start at"):
        await editor.trim(str(clip), str(tmp_path / "a.mp4"), start=-2.0)
    with pytest.raises(OperationFailed, match="no length"):
        await editor.trim(str(clip), str(tmp_path / "b.mp4"), start=5.0, end=5.0)


async def test_concatenating_one_clip_is_refused(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="at least two"):
        await editor.concat([str(clip)], str(tmp_path / "out.mp4"))


async def test_dubbing_nothing_is_refused(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    editor = FFmpegEditor(ffmpeg="/usr/bin/true")
    with pytest.raises(OperationFailed, match="narration, music, or both"):
        await editor.dub(str(clip), str(tmp_path / "out.mp4"))


# ----------------------------------------------------------------------------- the two ports


def test_both_adapters_satisfy_the_port():
    assert isinstance(FFmpegEditor(), MediaEditor)
    assert isinstance(UnavailableEditor(), MediaEditor)


def test_the_unavailable_editor_refuses_every_operation_with_a_remedy():
    """A capability gap the owner can act on, rather than one they have to guess at."""
    editor = UnavailableEditor()
    assert "ffmpeg" in editor.reason and "Nothing was changed" in editor.reason
    operations = [
        name
        for name, member in inspect.getmembers(UnavailableEditor, inspect.isfunction)
        if inspect.iscoroutinefunction(member) and not name.startswith("_")
    ]
    assert len(operations) == 12, f"an operation was added without a refusal: {operations}"


def test_no_command_in_the_adapter_is_built_as_a_shell_string():
    """Filenames and subtitle text reach this module from files, models and speech. A shell
    anywhere here would make a filename containing `;` into a command."""
    source = Path("packages/media/thursday_media/ffmpeg.py").read_text(encoding="utf-8")
    assert "create_subprocess_shell" not in source
    assert "shell=True" not in source
    assert "os.system" not in source
