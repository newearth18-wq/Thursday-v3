"""V11 acceptance: a promotional video, actually rendered (§15).

The brief's headline request is *"make a short promotional video for tomorrow's school event
using the information in my project folder."* This file is the part of that which Thursday
can do on a machine with no model credentials and no network: take a script and some
pictures, and produce a real MP4 with narration, music and burnt-in subtitles.

What makes it an acceptance test rather than an integration test is that every assertion is
made against **the finished file**, opened and read back — not against a return value, an
exit code, or a step that claimed to have run. A test that asserted `report.ok` would pass
against an implementation that wrote nothing at all.

Skipped without ffmpeg, which is the same condition under which Thursday says it cannot
edit video here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_media.creative import PromoRequest, Scene, compose, expectations
from thursday_media.ffmpeg import FFmpegEditor, discover
from thursday_media.plan import render
from thursday_media.quality import check_output
from thursday_media.subtitles import parse_srt
from thursday_media.tools import MediaEditTool, undo_media_edit
from thursday_shared.models import ToolCall

FFMPEG, _ = discover()
pytestmark = pytest.mark.skipif(not FFMPEG, reason="no ffmpeg on this machine")

SCRIPT = [
    "พรุ่งนี้โรงเรียนของเรามีงานเปิดบ้านวิชาการ",
    "มีนิทรรศการจากทุกกลุ่มสาระการเรียนรู้",
    "Everyone is welcome — the hall opens at nine",
]


@pytest.fixture
def editor(tmp_path: Path) -> FFmpegEditor:
    return FFmpegEditor.discovered(allowed_roots=(tmp_path,))


async def _ffmpeg(editor: FFmpegEditor, args: list[str]) -> None:
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


async def _card(editor: FFmpegEditor, path: Path, colour: str) -> str:
    """A storyboard frame. In a real run these are photographs or generated images."""
    await _ffmpeg(
        editor,
        ["-f", "lavfi", "-i", f"color=c={colour}:size=1280x720", "-frames:v", "1", str(path)],
    )
    return str(path)


async def _voice(editor: FFmpegEditor, path: Path, seconds: float, hz: int) -> str:
    """Stands in for narration audio. Thursday has no TTS in this container, so the test
    supplies the audio the way a real run would — as an input."""
    await _ffmpeg(
        editor,
        ["-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}", "-c:a", "aac", str(path)],
    )
    return str(path)


# ----------------------------------------------------------------- the headline scenario


async def test_a_promotional_video_is_assembled_narrated_and_subtitled(editor, tmp_path):
    """Script + pictures + narration → one MP4 that holds all of it."""
    work = tmp_path / "work"
    work.mkdir()
    colours = ["navy", "darkgreen", "maroon"]
    lengths = [2.5, 3.0, 2.0]

    scenes = [
        Scene(
            text=line,
            image=await _card(editor, work / f"card{i}.png", colours[i]),
            narration=await _voice(editor, work / f"line{i}.m4a", lengths[i], 300 + i * 100),
        )
        for i, line in enumerate(SCRIPT)
    ]
    music = await _voice(editor, work / "music.m4a", 4.0, 150)

    request = PromoRequest(
        name="openhouse",
        title="Open House",
        scenes=scenes,
        workdir=work,
        aspect="9:16",
        music=music,
        music_gain_db=-22.0,
    )
    build = await compose(request, editor)

    assert build.ready, build.describe()
    # Narration per scene was supplied, so every cue sits on the frame the voice starts.
    assert build.subtitles.measured
    assert build.seconds == pytest.approx(sum(lengths), abs=0.3)

    report = await render(build.plan, editor)
    assert report.ok, report.describe()

    # --- everything below reads the finished file, not the report ---
    probe = await editor.probe(report.deliverable)
    assert Path(report.deliverable).exists()
    assert (probe.video.width, probe.video.height) == (1080, 1920), "asked for vertical"
    assert probe.has_audio, "narration and music were asked for"
    assert probe.seconds == pytest.approx(sum(lengths), abs=1.0)

    quality = check_output(
        probe,
        target=request.target,
        expect_audio=True,
        subtitles=parse_srt(Path(build.subtitle_path).read_text(encoding="utf-8")),
        min_seconds=sum(lengths) - 1.0,
    )
    assert quality.ok, quality.describe()
    assert quality.certain

    # The subtitles are pixels in the picture, not a claim in a report.
    with_subs = await editor.thumbnail(report.deliverable, str(work / "check.png"), at=1.0)
    plain = await editor.still(
        scenes[0].image, str(work / "plain.mp4"), seconds=1.0, target=request.target
    )
    plain_frame = await editor.thumbnail(plain, str(work / "plain.png"), at=0.5)
    assert Path(with_subs).stat().st_size > Path(plain_frame).stat().st_size

    # And a cover image was produced alongside it.
    assert (work / "openhouse-thumbnail.png").stat().st_size > 0


async def test_one_narration_file_gives_estimated_timings_and_says_so(editor, tmp_path):
    """The same video, narrated in one take. Perfectly good output — and a different claim
    about the subtitles, which the track records and the quality gate repeats."""
    work = tmp_path / "work"
    work.mkdir()
    scenes = [
        Scene(text=line, image=await _card(editor, work / f"c{i}.png", "teal"))
        for i, line in enumerate(SCRIPT)
    ]
    request = PromoRequest(
        name="single",
        scenes=scenes,
        workdir=work,
        aspect="16:9",
        narration=await _voice(editor, work / "vo.m4a", 8.0, 320),
    )
    build = await compose(request, editor)

    assert build.ready
    assert not build.subtitles.measured
    assert "estimated" in build.describe()

    report = await render(build.plan, editor)
    assert report.ok, report.describe()

    probe = await editor.probe(report.deliverable)
    assert probe.has_video and probe.has_audio
    quality = check_output(
        probe, target=request.target, expect_audio=True, subtitles=build.subtitles
    )
    subtitle_check = next(c for c in quality.checks if c.name == "subtitles")
    assert subtitle_check.state == "pass"
    assert "reading speed" in subtitle_check.detail, "an estimate must not read as synced"


# ------------------------------------------------------------------- refusing to pretend


async def test_a_request_with_no_pictures_says_what_it_needs(editor, tmp_path):
    """`ready=False` before anything is rendered, rather than a failure four steps in."""
    build = await compose(
        PromoRequest(name="bare", scenes=[Scene(text=line) for line in SCRIPT], workdir=tmp_path),
        editor,
    )
    assert not build.ready
    assert not build.plan.steps
    assert any("picture for scene 1, 2, 3" in m for m in build.missing)


async def test_a_missing_input_file_is_caught_before_the_render(editor, tmp_path):
    build = await compose(
        PromoRequest(
            name="ghost",
            scenes=[Scene(text="hello", image=str(tmp_path / "nothing.png"))],
            workdir=tmp_path,
        ),
        editor,
    )
    assert not build.ready
    assert any("does not exist" in m for m in build.missing)


async def test_narration_for_some_scenes_but_not_others_is_refused(editor, tmp_path):
    """A track that is part measured and part guessed is a guessed track — everything after
    the first guess has moved — so this is refused rather than quietly downgraded."""
    work = tmp_path / "w"
    work.mkdir()
    scenes = [
        Scene(
            text="one",
            image=await _card(editor, work / "a.png", "navy"),
            narration=await _voice(editor, work / "a.m4a", 1.0, 300),
        ),
        Scene(text="two", image=await _card(editor, work / "b.png", "navy")),
    ]
    build = await compose(PromoRequest(name="mixed", scenes=scenes, workdir=work), editor)
    assert not build.ready
    assert any("every scene or none" in m for m in build.missing)


# --------------------------------------------------------------- through the tool surface


async def test_the_edit_tool_verifies_against_the_finished_file(editor, tmp_path):
    """`verified` is the quality gate's verdict. This is what the Supervisor reads."""
    work = tmp_path / "w"
    work.mkdir()
    scenes = [
        Scene(text=SCRIPT[0], image=await _card(editor, work / "a.png", "navy"), seconds=2.0),
        Scene(text=SCRIPT[1], image=await _card(editor, work / "b.png", "olive"), seconds=2.0),
    ]
    request = PromoRequest(name="tool", scenes=scenes, workdir=work, aspect="1:1")
    build = await compose(request, editor)

    tool = MediaEditTool(editor)
    result = await tool.run(
        ToolCall(
            tool="media.edit",
            args={"plan": build.plan.to_dict(), "expect": expectations(request, build)},
        ),
        ctx=None,
    )

    assert result.ok and result.verified, result.error
    assert result.data["quality"]["ok"] is True
    produced = Path(result.data["deliverable"])
    assert produced.exists() and produced.stat().st_size > 1024
    probe = await editor.probe(str(produced))
    assert (probe.video.width, probe.video.height) == (1080, 1080)


async def test_a_render_in_the_wrong_shape_comes_back_unverified(editor, tmp_path):
    """Work was done and it is not what was asked for. `ok=True, verified=False` — the
    distinction the whole verification layer exists to make."""
    work = tmp_path / "w"
    work.mkdir()
    scenes = [
        Scene(text="x", image=await _card(editor, work / "a.png", "navy"), seconds=1.5),
        Scene(text="y", image=await _card(editor, work / "b.png", "navy"), seconds=1.5),
    ]
    request = PromoRequest(name="wrong", scenes=scenes, workdir=work, aspect="16:9")
    build = await compose(request, editor)

    expect = expectations(request, build)
    expect["preset"] = "9:16"  # ask for a check the render was never going to satisfy

    result = await MediaEditTool(editor).run(
        ToolCall(tool="media.edit", args={"plan": build.plan.to_dict(), "expect": expect}),
        ctx=None,
    )
    assert result.ok, "the render itself succeeded"
    assert not result.verified, "and it is not what was asked for"
    assert "dimensions" in str(result.error) or "1920" in str(result.error)


async def test_an_edit_can_be_undone_by_deleting_only_what_it_made(editor, tmp_path):
    """Safe because an edit never overwrites an input — everything it names, it created."""
    work = tmp_path / "w"
    work.mkdir()
    original = await _card(editor, work / "keep.png", "navy")
    scenes = [
        Scene(text="a", image=original, seconds=1.0),
        Scene(text="b", image=original, seconds=1.0),
    ]
    build = await compose(PromoRequest(name="undo", scenes=scenes, workdir=work), editor)

    result = await MediaEditTool(editor).run(
        ToolCall(tool="media.edit", args={"plan": build.plan.to_dict()}), ctx=None
    )
    assert result.ok and result.undo is not None
    created = [Path(p) for p in result.undo.args["created"]]
    assert created and all(p.exists() for p in created)

    assert await undo_media_edit(result.undo)
    assert not any(p.exists() for p in created)
    assert Path(original).exists(), "the original picture must survive the undo"


async def test_a_long_render_resumes_after_a_restart(editor, tmp_path):
    """The brief's checkpoint case, against real files: ✓ scenes ✓ join ✗ subtitles."""
    work = tmp_path / "w"
    work.mkdir()
    scenes = [
        Scene(text=SCRIPT[i], image=await _card(editor, work / f"c{i}.png", "navy"), seconds=1.5)
        for i in range(3)
    ]
    build = await compose(PromoRequest(name="resume", scenes=scenes, workdir=work), editor)
    marks = work / "checkpoint.json"

    # Stop after the join by cutting the plan short, the way a killed process would leave it.
    partial = build.plan.to_dict()
    cut = [s for s in partial["steps"] if s["id"].startswith("scene") or s["id"] == "join"]
    from thursday_media.plan import EditPlan

    first = await render(EditPlan.from_dict({**partial, "steps": cut}), editor, checkpoint=marks)
    assert first.ok

    second = await render(build.plan, editor, checkpoint=marks)
    assert second.ok, second.describe()
    assert second.resumed == len(cut), "finished scenes should not be rendered twice"
    assert any(not o.resumed for o in second.outcomes), "the rest must still run"

    probe = await editor.probe(second.deliverable)
    assert probe.has_video and probe.seconds == pytest.approx(4.5, abs=0.5)
