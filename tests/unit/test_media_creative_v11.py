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
