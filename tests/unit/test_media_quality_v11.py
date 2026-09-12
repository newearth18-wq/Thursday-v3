"""The quality gate, and the three-state verdict it returns (V11).

`unknown` is what most of this file is about. A probe that could not read a file tells you
nothing about whether the render was good, and reporting that as a pass is the exact
failure mode this project is organised against.
"""

from __future__ import annotations

from thursday_media.ports import MediaProbe, StreamInfo, preset
from thursday_media.quality import check_output
from thursday_media.subtitles import Cue, SubtitleTrack, from_lines

VIDEO = StreamInfo(kind="video", codec="h264", width=1080, height=1920, fps=30.0)
AUDIO = StreamInfo(kind="audio", codec="aac", sample_rate=44100, channels=2)


def _probe(**kwargs) -> MediaProbe:
    base = {
        "path": "/tmp/out.mp4",
        "seconds": 12.0,
        "size_bytes": 2_000_000,
        "streams": (VIDEO, AUDIO),
    }
    return MediaProbe(**{**base, **kwargs})


def test_a_good_render_passes_everything():
    report = check_output(
        _probe(), target=preset("9:16"), expect_audio=True, min_seconds=5.0, max_seconds=30.0
    )
    assert report.ok
    assert report.certain
    assert report.failures == []


def test_an_empty_file_fails_and_nothing_else_is_judged():
    report = check_output(_probe(size_bytes=0), target=preset("9:16"), expect_audio=True)
    assert not report.ok
    assert [c.name for c in report.checks] == ["not_corrupt"]


def test_a_container_header_with_no_content_is_corrupt():
    """x264 writes a valid header before it writes a frame, so a killed render leaves
    exactly this behind — a file that exists and plays nothing."""
    report = check_output(_probe(size_bytes=400))
    assert not report.ok
    assert "header" in report.failures[0].detail


def test_a_file_with_no_streams_is_corrupt():
    report = check_output(_probe(streams=()))
    assert not report.ok
    assert report.failures[0].name == "not_corrupt"


def test_the_wrong_frame_size_fails_with_both_numbers_in_the_message():
    landscape = StreamInfo(kind="video", codec="h264", width=1920, height=1080)
    report = check_output(_probe(streams=(landscape, AUDIO)), target=preset("9:16"))
    failure = next(c for c in report.checks if c.name == "dimensions")
    assert failure.state == "fail"
    assert "1920×1080" in failure.detail and "1080×1920" in failure.detail


def test_an_unreadable_frame_size_is_unknown_rather_than_wrong():
    blind = StreamInfo(kind="video", codec="h264")
    report = check_output(_probe(streams=(blind,)), target=preset("16:9"))
    assert not report.ok, "an unknown is not a pass"
    assert not report.certain
    assert [c.name for c in report.unknowns] == ["dimensions"]


def test_missing_audio_fails_only_when_audio_was_expected():
    silent = _probe(streams=(VIDEO,))
    assert check_output(silent, expect_audio=False).ok
    assert not check_output(silent, expect_audio=True).ok


def test_a_video_shorter_than_asked_for_fails():
    report = check_output(_probe(seconds=3.0), min_seconds=10.0)
    assert not report.ok
    assert "shorter" in report.failures[0].detail


def test_a_video_longer_than_asked_for_fails():
    report = check_output(_probe(seconds=45.0), max_seconds=30.0)
    assert not report.ok
    assert "longer" in report.failures[0].detail


def test_a_duration_that_could_not_be_read_is_unknown():
    report = check_output(_probe(seconds=None), min_seconds=5.0)
    assert not report.certain
    assert report.unknowns[0].name == "duration"


def test_a_cue_past_the_end_of_the_video_fails():
    """The usual symptom of narration timed against a script that was later shortened."""
    track = SubtitleTrack(cues=[Cue(0.0, 2.0, "a"), Cue(30.0, 34.0, "b")])
    report = check_output(_probe(seconds=12.0), subtitles=track)
    failure = next(c for c in report.checks if c.name == "subtitles")
    assert failure.state == "fail"
    assert "past the end" in failure.detail


def test_an_empty_subtitle_track_fails():
    report = check_output(_probe(), subtitles=SubtitleTrack(cues=[]))
    assert not report.ok
    assert "no cues" in report.failures[0].detail


def test_estimated_timings_pass_but_say_so():
    """Not a failure — estimated timings are legitimate output. They are simply not the
    same claim as synchronised ones, and the owner is told which they have."""
    report = check_output(_probe(), subtitles=from_lines(["สวัสดี", "hello"]))
    check = next(c for c in report.checks if c.name == "subtitles")
    assert check.state == "pass"
    assert "reading speed" in check.detail
    assert report.ok


def test_measured_timings_are_reported_as_synced():
    report = check_output(_probe(), subtitles=from_lines(["a"], durations=[2.0]))
    check = next(c for c in report.checks if c.name == "subtitles")
    assert "synced" in check.detail


def test_subtitles_cannot_be_judged_against_an_unreadable_duration():
    report = check_output(_probe(seconds=None), subtitles=from_lines(["a"], durations=[2.0]))
    names = {c.name: c.state for c in report.checks}
    assert names["subtitles"] == "unknown"
    assert not report.ok


def test_the_report_serialises_for_the_task_view():
    report = check_output(_probe(), target=preset("9:16"), expect_audio=True)
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["certain"] is True
    assert {c["name"] for c in payload["checks"]} >= {"not_corrupt", "has_video", "dimensions"}
