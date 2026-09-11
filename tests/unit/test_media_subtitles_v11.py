"""Subtitle timing is arithmetic, so it is asserted exactly (V11).

Every timestamp here is a specific number rather than a range. A cue that drifts forty
milliseconds is invisible in a single assertion written as "roughly one second" and is a
full second out by the end of a two-minute narration, which is the bug this file exists to
catch while it is still small.
"""

from __future__ import annotations

import pytest
from thursday_media.subtitles import (
    MAX_CHARS_PER_LINE,
    Cue,
    SubtitleTrack,
    from_lines,
    is_unspaced_script,
    parse_srt,
    speaking_seconds,
    to_srt,
    to_vtt,
    wrap,
)


def test_a_cue_cannot_end_before_it_starts():
    with pytest.raises(ValueError, match="ends before it starts"):
        Cue(4.0, 1.0, "backwards")


def test_thai_is_counted_in_characters_and_english_in_words():
    """Thai has no spaces between words, so splitting on them finds one enormous 'word'.
    Counting characters is the only estimate available."""
    assert is_unspaced_script("สวัสดีครับ")
    assert not is_unspaced_script("hello there")

    # 20 speakable characters at 10/s.
    assert speaking_seconds("ก" * 20) == pytest.approx(2.0)
    # 15 words at 150wpm is six seconds.
    assert speaking_seconds(" ".join(["word"] * 15)) == pytest.approx(6.0)


def test_no_cue_is_shorter_than_the_eye_can_follow():
    assert speaking_seconds("ok") >= 0.8


def test_wrapping_keeps_lines_on_screen():
    line = wrap("Everyone at the school is warmly invited to tomorrow's opening ceremony")
    assert all(len(part) <= MAX_CHARS_PER_LINE for part in line.split("\n"))
    assert len(line.split("\n")) <= 2
    # Latin text breaks between words, never inside one.
    assert "  " not in line


def test_thai_wraps_on_characters_because_there_is_nothing_else_to_wrap_on():
    line = wrap("ก" * 90)
    parts = line.split("\n")
    assert len(parts) == 2
    assert all(len(p) <= MAX_CHARS_PER_LINE for p in parts)


def test_estimated_and_measured_tracks_are_never_confused():
    """The distinction the quality checker depends on: a track timed by reading speed is a
    guess, and one timed against real audio durations is not."""
    guessed = from_lines(["หนึ่ง", "สอง"])
    assert guessed.timing == "estimated"
    assert not guessed.measured
    assert "estimated" in guessed.describe()

    measured = from_lines(["หนึ่ง", "สอง"], durations=[1.5, 2.25])
    assert measured.timing == "measured"
    assert measured.measured
    assert "synced" in measured.describe()


def test_measured_timings_are_used_exactly():
    """A measured duration is how long the audio *is*. Clipping it to the readability cap
    would put the cue out of sync with the voice saying it."""
    track = from_lines(["a", "b"], durations=[9.0, 1.0], gap=0.0)
    assert track.cues[0].start == 0.0
    assert track.cues[0].end == 9.0
    assert track.cues[1].start == 9.0
    assert track.cues[1].end == 10.0


def test_guessed_timings_are_capped_for_readability():
    long_line = " ".join(["word"] * 100)  # 40 seconds at 150wpm
    track = from_lines([long_line])
    assert track.cues[0].seconds == 6.0


def test_a_track_cannot_be_part_measured_and_part_guessed():
    with pytest.raises(ValueError, match="part measured and part guessed"):
        from_lines(["one", "two"], durations=[1.0])


def test_blank_lines_produce_no_cue():
    track = from_lines(["real", "   ", "", "also real"], gap=0.0)
    assert len(track.cues) == 2


def test_srt_timestamps_are_exact():
    track = SubtitleTrack(cues=[Cue(0.0, 1.5, "หนึ่ง"), Cue(61.25, 3661.007, "two")])
    srt = to_srt(track)
    assert "00:00:00,000 --> 00:00:01,500" in srt
    assert "00:01:01,250 --> 01:01:01,007" in srt
    # Numbered from one, and each block separated by a blank line.
    assert srt.startswith("1\n")
    assert "\n2\n" in srt


def test_rounding_never_makes_a_cue_end_after_the_next_one_starts():
    """Truncating rather than rounding turned 1.9999 into 00:00:01,999 and left the next
    cue starting before this one ended."""
    track = SubtitleTrack(cues=[Cue(0.0, 1.9999, "a"), Cue(2.0, 3.0, "b")])
    lines = to_srt(track).splitlines()
    assert "00:00:02,000" in lines[1]


def test_vtt_has_its_header_and_a_dot_separator():
    vtt = to_vtt(SubtitleTrack(cues=[Cue(0.0, 1.0, "x")]))
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.000" in vtt


def test_srt_round_trips():
    original = from_lines(
        ["สวัสดีครับ ยินดีต้อนรับสู่งานของโรงเรียน", "See you tomorrow at nine"],
        durations=[3.25, 2.5],
    )
    back = parse_srt(to_srt(original))
    assert len(back.cues) == len(original.cues)
    for wrote, read in zip(original.cues, back.cues, strict=True):
        assert read.start == pytest.approx(wrote.start, abs=0.001)
        assert read.end == pytest.approx(wrote.end, abs=0.001)
        assert read.text == wrote.text


def test_parsing_an_empty_or_broken_file_gives_an_empty_track_rather_than_raising():
    assert parse_srt("").cues == []
    assert parse_srt("this is not a subtitle file").cues == []


def test_a_track_can_be_moved_along_the_timeline():
    """Narration usually starts after a title card, and the whole track moves with it."""
    track = from_lines(["a", "b"], durations=[1.0, 1.0], gap=0.0).shifted(2.5)
    assert track.cues[0].start == 2.5
    assert track.cues[1].end == 4.5
    assert track.measured, "shifting a measured track must not turn it into a guess"


def test_a_blank_line_with_a_duration_still_advances_the_clock():
    """A scene with a picture and nothing said over it is a slot on the timeline — a title
    card, a logo sting. Swallowing its time pulled every cue after it early."""
    track = from_lines(["first", "", "third"], durations=[2.0, 3.0, 2.0], gap=0.0)
    assert len(track.cues) == 2
    assert track.cues[0].start == 0.0
    assert track.cues[1].start == 5.0, "the silent middle scene occupies its three seconds"
    assert track.cues[1].end == 7.0


def test_supplied_durations_can_still_be_labelled_an_estimate():
    """Whether durations were passed in and where they came from are different questions.
    Scene lengths guessed from reading speed are a guess however they reach the track."""
    track = from_lines(["a", "b"], durations=[2.0, 2.0], timing="estimated")
    assert track.cues[1].start == pytest.approx(2.08)
    assert not track.measured
    assert "estimated" in track.describe()


def test_an_unknown_timing_label_is_refused():
    with pytest.raises(ValueError, match="'measured' or 'estimated'"):
        from_lines(["a"], durations=[1.0], timing="probably fine")
