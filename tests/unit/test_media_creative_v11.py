"""Composing a promotional video into a plan, with no ffmpeg and no render (V11).

The brief's headline scenario, taken apart: what `compose` will build, what it refuses to
build, and — the part worth the most — that the subtitles it produces belong to the scenes
they are laid over.
"""

from __future__ import annotations

import pytest
from thursday_media.creative import (
    DEFAULT_SCENE_SECONDS,
    PromoRequest,
    Scene,
    compose,
    expectations,
)
from thursday_media.ports import MediaProbe


class Lengths:
    """An editor that knows only how long things are. Enough for `compose`, which uses an
    editor for exactly one thing: reading narration durations."""

    name = "lengths"
    available = True

    def __init__(self, seconds: dict[str, float]) -> None:
        self._seconds = seconds

    async def probe(self, path: str) -> MediaProbe:
        return MediaProbe(path=path, seconds=self._seconds.get(path), size_bytes=4096)


def _scene(name: str, text: str, tmp_path, **kwargs) -> Scene:
    image = tmp_path / f"{name}.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return Scene(text=text, image=str(image), **kwargs)


# --------------------------------------------------------------------------- what it builds


async def test_a_script_and_pictures_become_an_ordered_plan(tmp_path):
    scenes = [_scene("a", "หนึ่ง", tmp_path, seconds=2.0), _scene("b", "two", tmp_path, seconds=3.0)]
    build = await compose(PromoRequest(name="promo", scenes=scenes, workdir=tmp_path))

    assert build.ready
    assert [s.op for s in build.plan.steps] == [
        "still",
        "still",
        "concat",
        "burn_subtitles",
        "thumbnail",
    ]
    assert build.seconds == pytest.approx(5.0)
    assert build.plan.final.endswith("promo-subtitled.mp4")


async def test_per_scene_narration_is_laid_on_before_the_join(tmp_path):
    """Each scene is exactly as long as the line spoken over it, so every cue lands on the
    frame the voice starts."""
    narration = {}
    scenes = []
    for name, text, seconds in (("a", "หนึ่ง", 2.5), ("b", "two", 1.75)):
        audio = tmp_path / f"{name}.m4a"
        audio.write_bytes(b"0" * 128)
        narration[str(audio)] = seconds
        scenes.append(_scene(name, text, tmp_path, narration=str(audio)))

    build = await compose(
        PromoRequest(name="p", scenes=scenes, workdir=tmp_path), Lengths(narration)
    )

    assert [s.op for s in build.plan.steps] == [
        "still",
        "dub",
        "still",
        "dub",
        "concat",
        "burn_subtitles",
        "thumbnail",
    ]
    assert build.seconds == pytest.approx(4.25)
    assert build.subtitles.measured
    # The scene is as long as its narration, not as long as reading its text would take.
    assert build.plan.steps[0].args["seconds"] == pytest.approx(2.5)
    assert build.plan.steps[2].args["seconds"] == pytest.approx(1.75)


async def test_a_subtitle_cue_never_outlives_the_video_it_belongs_to(tmp_path):
    """The bug this guards: scene lengths came from `seconds` and cue lengths were
    re-derived from reading speed, so three Thai lines over a four-second video produced
    eight seconds of subtitles — a track running past its own picture."""
    long_thai = "พรุ่งนี้โรงเรียนของเรามีงานเปิดบ้านวิชาการและนิทรรศการจากทุกกลุ่มสาระ"
    scenes = [
        _scene("a", long_thai, tmp_path, seconds=2.0),
        _scene("b", long_thai, tmp_path, seconds=2.0),
    ]
    build = await compose(PromoRequest(name="p", scenes=scenes, workdir=tmp_path))

    assert build.subtitles.seconds <= build.seconds
    assert build.subtitles.cues[0].seconds == pytest.approx(2.0)
    assert build.subtitles.cues[1].start == pytest.approx(2.0)


async def test_a_scene_with_a_picture_and_no_words_still_takes_its_time(tmp_path):
    scenes = [_scene("a", "", tmp_path, seconds=2.0), _scene("b", "spoken", tmp_path, seconds=2.0)]
    build = await compose(PromoRequest(name="p", scenes=scenes, workdir=tmp_path))
    assert len(build.subtitles.cues) == 1
    assert build.subtitles.cues[0].start == pytest.approx(2.0), "the silent scene ran first"


async def test_a_scene_with_no_stated_length_is_timed_by_its_words(tmp_path):
    build = await compose(
        PromoRequest(name="p", scenes=[_scene("a", "ก" * 30, tmp_path)], workdir=tmp_path)
    )
    assert build.seconds == pytest.approx(3.0)  # 30 chars at 10/s


async def test_a_wordless_scene_with_no_length_gets_the_default(tmp_path):
    build = await compose(
        PromoRequest(name="p", scenes=[_scene("a", "", tmp_path)], workdir=tmp_path)
    )
    assert build.seconds == pytest.approx(DEFAULT_SCENE_SECONDS)


async def test_music_and_a_single_narration_become_one_dub_step(tmp_path):
    for name in ("vo.m4a", "bed.m4a"):
        (tmp_path / name).write_bytes(b"0" * 128)
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[
                _scene("a", "x", tmp_path, seconds=2.0),
                _scene("b", "y", tmp_path, seconds=2.0),
            ],
            workdir=tmp_path,
            narration=str(tmp_path / "vo.m4a"),
            music=str(tmp_path / "bed.m4a"),
            music_gain_db=-22.0,
        )
    )
    dub = next(s for s in build.plan.steps if s.op == "dub")
    assert dub.args["narration"].endswith("vo.m4a")
    assert dub.args["music_gain_db"] == -22.0
    assert not build.subtitles.measured, "one file for the whole script cannot time the lines"


async def test_subtitles_and_thumbnail_can_be_turned_off(tmp_path):
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[
                _scene("a", "x", tmp_path, seconds=1.0),
                _scene("b", "y", tmp_path, seconds=1.0),
            ],
            workdir=tmp_path,
            subtitles=False,
            thumbnail=False,
        )
    )
    assert [s.op for s in build.plan.steps] == ["still", "still", "concat"]
    assert build.subtitle_path == ""


async def test_the_cover_frame_is_never_taken_from_the_first_frame(tmp_path):
    """The first frame of a title card is usually flat, and a blank thumbnail is how a good
    video goes unwatched."""
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[
                _scene("a", "x", tmp_path, seconds=5.0),
                _scene("b", "y", tmp_path, seconds=5.0),
            ],
            workdir=tmp_path,
        )
    )
    thumbnail = next(s for s in build.plan.steps if s.op == "thumbnail")
    assert thumbnail.args["at"] >= 0.2


async def test_a_plan_that_composes_is_always_a_plan_that_validates(tmp_path):
    """`compose` validates before returning, so an invalid plan never reaches a renderer."""
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[
                _scene("a", "x", tmp_path, seconds=1.0),
                _scene("b", "y", tmp_path, seconds=1.0),
            ],
            workdir=tmp_path,
        )
    )
    build.plan.validate()
    produced = [s.produces for s in build.plan.steps]
    assert len(set(produced)) == len(produced), "two steps must not write the same file"


# ------------------------------------------------------------------------ what it refuses


async def test_an_empty_script_is_refused_with_a_reason(tmp_path):
    build = await compose(PromoRequest(name="p", scenes=[], workdir=tmp_path))
    assert not build.ready
    assert "no scenes" in build.missing[0]
    assert "missing" in build.describe()


async def test_missing_pictures_are_named_by_scene_number(tmp_path):
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[_scene("a", "x", tmp_path), Scene(text="y"), Scene(text="z")],
            workdir=tmp_path,
        )
    )
    assert not build.ready
    assert any("scene 2, 3" in m for m in build.missing)


async def test_both_kinds_of_narration_at_once_is_refused(tmp_path):
    audio = tmp_path / "vo.m4a"
    audio.write_bytes(b"0" * 128)
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[_scene("a", "x", tmp_path, narration=str(audio))],
            workdir=tmp_path,
            narration=str(audio),
        ),
        Lengths({str(audio): 1.0}),
    )
    assert not build.ready
    assert any("not both" in m for m in build.missing)


async def test_nothing_is_written_when_a_request_is_refused(tmp_path):
    """No half-made SRT left behind for somebody to find later."""
    await compose(PromoRequest(name="p", scenes=[Scene(text="x")], workdir=tmp_path))
    assert list(tmp_path.glob("*.srt")) == []


# ------------------------------------------------------------------------- expectations


async def test_expectations_come_from_the_request_and_not_from_the_render(tmp_path):
    """Asking the output what shape it is and then checking it is that shape proves
    nothing."""
    scenes = [_scene("a", "x", tmp_path, seconds=2.0), _scene("b", "y", tmp_path, seconds=2.0)]
    request = PromoRequest(name="p", scenes=scenes, workdir=tmp_path, aspect="9:16")
    build = await compose(request)
    expect = expectations(request, build)

    assert expect["preset"] == "9:16"
    assert expect["audio"] is False, "no narration and no music was supplied"
    assert expect["min_seconds"] <= build.seconds <= expect["max_seconds"]
    assert expect["subtitles"] == build.subtitle_path


async def test_expectations_know_audio_was_asked_for(tmp_path):
    (tmp_path / "bed.m4a").write_bytes(b"0" * 128)
    request = PromoRequest(
        name="p",
        scenes=[_scene("a", "x", tmp_path, seconds=1.0), _scene("b", "y", tmp_path, seconds=1.0)],
        workdir=tmp_path,
        music=str(tmp_path / "bed.m4a"),
    )
    assert expectations(request, await compose(request))["audio"] is True


# --------------------------------------------------------------- narration Thursday speaks


class SpeaksLines:
    """A narrator whose audio is as long as the text, so scene timing is predictable."""

    def __init__(self, *, available=True, seconds_per_char=0.1):
        self.available = available
        self._per_char = seconds_per_char
        self.asked: list[str] = []

    async def narrate(self, lines, workdir, *, prefix="line", voice=""):
        from pathlib import Path

        from thursday_media.narration import NarratedLine, Narration

        self.asked = list(lines)
        Path(workdir).mkdir(parents=True, exist_ok=True)
        result = Narration(voice=voice, backend="speaks-lines")
        for index, text in enumerate(lines, start=1):
            path = Path(workdir) / f"{prefix}{index:02d}.wav"
            path.write_bytes(b"RIFF")
            result.lines.append(
                NarratedLine(text=text, path=str(path), seconds=len(text) * self._per_char)
            )
        return result


async def test_thursday_speaking_the_script_gives_measured_timings(tmp_path):
    """The gap V11 left open and V12 closes: with nothing to narrate with, every video took
    the estimated path."""
    scenes = [_scene("a", "abcde", tmp_path), _scene("b", "abcdefghij", tmp_path)]
    request = PromoRequest(name="p", scenes=scenes, workdir=tmp_path, narrate=True, voice="th")
    build = await compose(request, None, SpeaksLines())

    assert build.ready, build.describe()
    assert build.subtitles.measured, "a spoken script is measured, not estimated"
    assert build.seconds == pytest.approx(1.5, abs=0.01)
    # Each scene is exactly as long as the line spoken over it.
    stills = [s for s in build.plan.steps if s.op == "still"]
    assert [s.args["seconds"] for s in stills] == pytest.approx([0.5, 1.0], abs=0.01)
    # And the narration is laid on scene by scene, before the join.
    assert [s.op for s in build.plan.steps][:4] == ["still", "dub", "still", "dub"]


async def test_what_was_spoken_is_reported(tmp_path):
    """A synthesised voice is something the owner should be told about, not left to
    notice."""
    scenes = [_scene("a", "one", tmp_path), _scene("b", "two", tmp_path)]
    build = await compose(
        PromoRequest(name="p", scenes=scenes, workdir=tmp_path, narrate=True, voice="th"),
        None,
        SpeaksLines(),
    )
    assert build.narration is not None
    assert build.narration.backend == "speaks-lines"
    assert build.to_dict()["narration"]["backend"] == "speaks-lines"
    assert build.to_dict()["narration"]["lines"][0]["text"] == "one"


async def test_narrating_without_a_voice_is_refused_with_the_remedy(tmp_path):
    build = await compose(
        PromoRequest(name="p", scenes=[_scene("a", "x", tmp_path)], workdir=tmp_path, narrate=True),
        None,
        None,
    )
    assert not build.ready
    assert any("THURSDAY_TTS_BACKEND=espeak" in m for m in build.missing)


async def test_an_unavailable_narrator_is_refused_before_anything_is_written(tmp_path):
    build = await compose(
        PromoRequest(name="p", scenes=[_scene("a", "x", tmp_path)], workdir=tmp_path, narrate=True),
        None,
        SpeaksLines(available=False),
    )
    assert not build.ready
    assert list(tmp_path.glob("*.srt")) == []


async def test_speaking_and_supplying_narration_at_once_is_refused(tmp_path):
    (tmp_path / "vo.m4a").write_bytes(b"0" * 128)
    build = await compose(
        PromoRequest(
            name="p",
            scenes=[_scene("a", "x", tmp_path)],
            workdir=tmp_path,
            narrate=True,
            narration=str(tmp_path / "vo.m4a"),
        ),
        None,
        SpeaksLines(),
    )
    assert not build.ready
    assert any("not both" in m for m in build.missing)


async def test_a_scene_with_no_line_cannot_be_narrated(tmp_path):
    """All or none, for the same reason a supplied track cannot be part measured: a silent
    scene would have to be timed by a different rule, and every cue after it would move."""
    scenes = [_scene("a", "spoken", tmp_path), _scene("b", "", tmp_path)]
    build = await compose(
        PromoRequest(name="p", scenes=scenes, workdir=tmp_path, narrate=True),
        None,
        SpeaksLines(),
    )
    assert not build.ready
    assert any("words for scene 2" in m for m in build.missing)


async def test_nothing_is_spoken_when_the_request_is_refused(tmp_path):
    """The check runs before a single line is synthesised — five files of audio nobody
    asked for is a mess somebody has to clean up."""
    narrator = SpeaksLines()
    await compose(
        PromoRequest(
            name="p",
            scenes=[_scene("a", "x", tmp_path), _scene("b", "", tmp_path)],
            workdir=tmp_path,
            narrate=True,
        ),
        None,
        narrator,
    )
    assert narrator.asked == [], "nothing should have been sent to the synthesiser"


async def test_the_narrators_own_measurement_is_used_without_an_editor(tmp_path):
    """`compose` is given `editor=None` here on purpose. The narrator measured these
    durations off the audio it just wrote, so nothing needs to probe the files again — and
    until this was checked, the measurement was silently discarded and every scene fell
    back to reading speed, which made a spoken script no better timed than a guessed one.
    """
    scenes = [_scene("a", "abcde", tmp_path), _scene("b", "abcdefghij", tmp_path)]
    build = await compose(
        PromoRequest(name="p", scenes=scenes, workdir=tmp_path, narrate=True),
        None,
        SpeaksLines(seconds_per_char=0.1),
    )

    assert build.narration.durations == pytest.approx([0.5, 1.0])
    assert build.seconds == pytest.approx(1.5, abs=0.001)
    stills = [s.args["seconds"] for s in build.plan.steps if s.op == "still"]
    assert stills == pytest.approx([0.5, 1.0], abs=0.001)
    # And the cues sit on the scenes, which is what "measured" is supposed to buy.
    assert [c.start for c in build.subtitles.cues] == pytest.approx([0.0, 0.5], abs=0.001)
