"""Subtitles nobody can read are not subtitles (§24) — Sprint 107.

[§23](../../docs/23-release-readiness.md) has carried this admission since the media sprint:

> **subtitle burn-in depends on the machine's fonts** — libass renders what fontconfig can
> find, so a system with no Thai font produces boxes. The quality gate cannot see that, and
> this document says so rather than letting the test suite's green imply otherwise.

Both halves are now measured rather than assumed. The same Thai line, burned twice on this
machine — once normally, once with fontconfig pointed at a directory holding one Latin font:

* with a Thai font, the frame reads `แมวของฉันชื่อมะลิ กินปลาทูเป็นอาหารโปรด`;
* without, it is a row of empty boxes;
* and `check_output` returned `ok=True, 2 checks passed` **for both files**.

The gate is not at fault: it reads the finished file, and a box is just pixels there. The
renderer is the layer that knows, and libass had already said so on stderr —

    fontselect: failed to find any fallback with glyph 0xE41

— which `_must_run` has returned since the day it was written, and `burn_subtitles` threw
away. ffmpeg then exits 0 having produced a perfectly valid video of nothing readable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from thursday_media.ffmpeg import FFmpegEditor, _refuse_unrenderable_text, discover
from thursday_media.ports import OperationFailed

from tests.fonts import thai_font_available

FFMPEG, FFPROBE = discover()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="no ffmpeg on this machine")
needs_thai_font = pytest.mark.skipif(
    not thai_font_available(), reason="no font on this machine can draw Thai"
)

THAI = "แมวของฉันชื่อมะลิ กินปลาทูเป็นอาหารโปรด"

#: What libass writes when it draws a box. The first line is not a failure — it is how
#: fallback begins — and only the second says it gave up.
TRYING = "[Parsed_subtitles_0] Glyph 0xE41 not found, selecting one more font for (Arial, 400, 0)"
GAVE_UP = "[Parsed_subtitles_0] fontselect: failed to find any fallback with glyph 0xE41"


# ----------------------------------------------------------------- reading what libass said


def test_a_glyph_that_fell_back_successfully_is_not_a_failure():
    """Fallback is the normal path: the first font lacks the character, the next has it, and
    the picture is correct. Refusing on the trying-line would refuse most correct renders."""
    _refuse_unrenderable_text(TRYING, subtitles=Path("subs.srt"))


def test_a_glyph_nothing_could_draw_is_refused():
    with pytest.raises(OperationFailed, match="no font on this machine can draw"):
        _refuse_unrenderable_text(f"{TRYING}\n{GAVE_UP}", subtitles=Path("subs.srt"))


def test_the_refusal_names_the_characters_and_what_to_do():
    """ "Something went wrong with fonts" sends the owner to a search engine. The script and
    the characters send them to a package name."""
    with pytest.raises(OperationFailed) as raised:
        _refuse_unrenderable_text(GAVE_UP, subtitles=Path("lesson.srt"))

    message = str(raised.value)
    assert "lesson.srt" in message
    assert "thai" in message
    assert "แ" in message, "the character itself, not only its code point"
    assert "Install a font" in message


def test_the_refusal_carries_the_code_points_for_a_machine_to_read():
    with pytest.raises(OperationFailed) as raised:
        _refuse_unrenderable_text(GAVE_UP, subtitles=Path("subs.srt"))
    assert raised.value.details["missing"] == ["U+0E41"]


def test_every_undrawable_character_is_reported_not_just_the_first():
    """An owner who installs a font for the one character in the message, renders again, and
    is told about the next one has been sent round a loop."""
    stderr = "\n".join(
        f"fontselect: failed to find any fallback with glyph 0x{code:X}"
        for code in (0x0E41, 0x0E21, 0x0E27)
    )
    with pytest.raises(OperationFailed) as raised:
        _refuse_unrenderable_text(stderr, subtitles=Path("subs.srt"))
    assert raised.value.details["missing"] == ["U+0E21", "U+0E27", "U+0E41"]


def test_a_clean_render_says_nothing():
    _refuse_unrenderable_text("frame= 75 fps=0.0 q=-1.0 Lsize=7kB", subtitles=Path("subs.srt"))


# ----------------------------------------------------------------- against real ffmpeg


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "subs.srt").write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{THAI}\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def starved(workspace: Path):
    """fontconfig pointed at a directory with one Latin font — a machine with no Thai font,
    which is the deployment §23 describes and which no test had ever stood up."""
    empty = workspace / "fonts"
    empty.mkdir()
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ):
        if Path(candidate).exists():
            (empty / Path(candidate).name).write_bytes(Path(candidate).read_bytes())
            break
    else:  # pragma: no cover - a machine with neither font is not one we can starve
        pytest.skip("no Latin-only font to starve fontconfig with")

    config = workspace / "fonts.conf"
    config.write_text(
        "<?xml version='1.0'?><fontconfig>"
        f"<dir>{empty}</dir><cachedir>{workspace / 'fccache'}</cachedir>"
        "</fontconfig>",
        encoding="utf-8",
    )
    previous = os.environ.get("FONTCONFIG_FILE")
    os.environ["FONTCONFIG_FILE"] = str(config)
    yield
    if previous is None:
        os.environ.pop("FONTCONFIG_FILE", None)
    else:
        os.environ["FONTCONFIG_FILE"] = previous


async def _blue_video(editor: FFmpegEditor, workspace: Path) -> Path:
    base = workspace / "base.mp4"
    await editor._must_run(
        ["-y", "-f", "lavfi", "-i", "color=c=navy:s=320x180:d=2", str(base)],
        what="making a test video",
    )
    return base


@needs_ffmpeg
@needs_thai_font
async def test_thai_subtitles_burn_in_on_a_machine_that_has_a_thai_font(workspace: Path):
    """The half §23 could only assume. This machine has `tlwg/Loma`, so the render should
    succeed — and if it stops succeeding, this says so rather than the frame quietly
    becoming boxes."""
    editor = FFmpegEditor(FFMPEG, FFPROBE, allowed_roots=(workspace,))
    base = await _blue_video(editor, workspace)

    out = await editor.burn_subtitles(
        str(base), str(workspace / "out.mp4"), subtitles=str(workspace / "subs.srt")
    )

    assert Path(out).exists()


@needs_ffmpeg
@pytest.mark.usefixtures("starved")
async def test_the_same_subtitles_are_refused_when_no_font_can_draw_them(workspace: Path):
    """The other half. ffmpeg exits 0 and writes a valid video; what it wrote is boxes."""
    editor = FFmpegEditor(FFMPEG, FFPROBE, allowed_roots=(workspace,))
    base = await _blue_video(editor, workspace)

    with pytest.raises(OperationFailed, match="empty boxes"):
        await editor.burn_subtitles(
            str(base), str(workspace / "out.mp4"), subtitles=str(workspace / "subs.srt")
        )


@needs_ffmpeg
@pytest.mark.usefixtures("starved")
async def test_latin_subtitles_still_render_on_the_starved_machine(workspace: Path):
    """The refusal is about characters, not about languages or about fontconfig being
    unusual. A machine with only a Latin font renders Latin perfectly well."""
    (workspace / "subs.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nMy cat is called Mali\n", encoding="utf-8"
    )
    editor = FFmpegEditor(FFMPEG, FFPROBE, allowed_roots=(workspace,))
    base = await _blue_video(editor, workspace)

    out = await editor.burn_subtitles(
        str(base), str(workspace / "out.mp4"), subtitles=str(workspace / "subs.srt")
    )

    assert Path(out).exists()
