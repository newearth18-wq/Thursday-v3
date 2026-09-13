"""Media editing through the whole system, not just through its own package (V11).

The unit and integration tests around `thursday_media` prove the editor works. This file
proves it is *wired* — that an edit goes through the Permission Engine, lands in the audit
chain, can be undone, and reports itself honestly through `health()` whether or not ffmpeg
is on the machine.

The two halves matter equally. A capability that works but bypasses authorisation is a hole;
a capability that refuses cleanly when it is absent is the difference between an assistant
that says "I can't do that here" and one that fails halfway through somebody's video.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_core.config import Settings
from thursday_core.container import build_container
from thursday_media.creative import PromoRequest, Scene, compose, expectations
from thursday_media.ffmpeg import discover
from thursday_media.unavailable import UnavailableEditor
from thursday_shared.enums import PermissionLevel, PolicyDecision, RiskLevel
from thursday_shared.models import ToolCall

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


# ------------------------------------------------------------------------ always, either way


def test_the_media_tools_are_registered(container):
    """Registered whether or not ffmpeg is present — unlike the browser tools, which vanish
    without Playwright. An unavailable editor still answers, with a remedy."""
    assert {"media.probe", "media.edit"} <= set(container.tools.names())


def test_the_agent_can_reach_exactly_the_media_tools_it_declares(container):
    spec = container.agents.get("media").spec
    assert "media.edit" in spec.tools
    assert spec.permission_ceiling is PermissionLevel.MODIFY, "editing writes files"


def test_editing_is_authorised_as_a_modification_and_not_as_a_deletion(container):
    """An edit only ever adds files, so it is `MODIFY`/`LOW`/`AUTO`. Asking before every
    render would be approval fatigue for an operation that cannot destroy anything."""
    policy = container.policy.get("media.edit")
    assert policy.level is PermissionLevel.MODIFY
    assert policy.default is PolicyDecision.AUTO
    assert policy.risk is RiskLevel.LOW
    assert policy.reversible, "undoing an edit is deleting what the call created"

    read = container.policy.get("media.probe")
    assert read.level is PermissionLevel.READ


def test_editing_cannot_be_granted_its_way_past_the_block_set(container):
    """The BLOCK set has no override path, and nothing added in V11 changed that."""
    from thursday_security.policy import HARD_BLOCKED

    assert "media.edit" not in HARD_BLOCKED
    assert container.policy.get("audit.delete").default is PolicyDecision.BLOCK


async def test_health_reports_media_editing_either_way(container):
    checks = {c["component"]: c for c in await container.health()}
    assert "media" in checks
    # Not being able to edit video is not a fault — a machine without ffmpeg is a supported
    # deployment. Claiming the capability would be the fault.
    assert checks["media"]["ok"] is True
    assert checks["media"]["detail"]


def test_the_undo_executor_is_wired(container):
    assert container.undo.get is not None
    assert "media_edit" in container.undo._executors


# ------------------------------------------------------------------- with no ffmpeg at all


async def test_without_ffmpeg_the_tool_refuses_with_a_remedy_and_writes_nothing(
    tmp_path, monkeypatch
):
    """The brief's hardest rule, end to end: never show progress and then report a
    completion that did not happen.

    Built through the real container with discovery made to find nothing, rather than by
    swapping the editor afterwards — the point is that `_build_editor` takes the right
    branch on a machine with no ffmpeg, which a hand-placed `UnavailableEditor` would not
    prove.
    """
    monkeypatch.setattr("thursday_media.ffmpeg.discover", lambda explicit=None: ("", ""))
    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
            log_level="WARNING",
            llm_backend="rule",
            vault_backend="memory",
        ),
        configure_logs=False,
    )
    assert isinstance(container.editor, UnavailableEditor)
    assert not container.editor.available
    # Still registered, so the owner gets a reason instead of ToolNotFound.
    assert {"media.probe", "media.edit"} <= set(container.tools.names())

    checks = {c["component"]: c for c in await container.health()}
    assert "not installed" in checks["media"]["detail"]
    assert container.agents.get("media").describe_capabilities()["edit"] is False

    plan = {
        "name": "p",
        "steps": [
            {
                "id": "a",
                "op": "still",
                "produces": str(tmp_path / "out.mp4"),
                "args": {"image": str(tmp_path / "in.png"), "seconds": 2},
            }
        ],
    }
    result = await container.executor.execute(
        ToolCall(tool="media.edit", args={"plan": plan}, reason="render"), agent="media"
    )

    assert not result.ok
    assert not result.verified
    assert "ffmpeg" in str(result.error)
    assert "Nothing was changed" in str(result.error)
    assert not (tmp_path / "out.mp4").exists()


# ------------------------------------------------------------------------ with a real ffmpeg


@needs_ffmpeg
@needs_thai_font
async def test_an_edit_passes_the_permission_engine_and_lands_in_the_audit_chain(tmp_path):
    """The path that matters: nothing edits a file without going through Authorize, and
    everything that did is in the hash chain afterwards."""
    work = tmp_path / "var" / "media"
    work.mkdir(parents=True)
    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
            log_level="WARNING",
            llm_backend="rule",
            vault_backend="memory",
        ),
        configure_logs=False,
    )
    assert container.editor.available, "this test needs the real editor"

    # Source material, built with the same binary.
    images = []
    for index, colour in enumerate(("navy", "olive")):
        path = work / f"card{index}.png"
        process = await asyncio.create_subprocess_exec(
            container.editor.ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={colour}:size=640x360",
            "-frames:v",
            "1",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.communicate()
        images.append(str(path))

    request = PromoRequest(
        name="wired",
        scenes=[
            Scene(text="หนึ่ง", image=images[0], seconds=1.5),
            Scene(text="two", image=images[1], seconds=1.5),
        ],
        workdir=work,
        aspect="1:1",
    )
    build = await compose(request, container.editor)
    assert build.ready, build.describe()

    before = len(container.audit.entries())
    result = await container.executor.execute(
        ToolCall(
            tool="media.edit",
            args={"plan": build.plan.to_dict(), "expect": expectations(request, build)},
            reason="render the school promo",
        ),
        agent="media",
    )

    assert result.ok, result.error
    assert result.verified, f"the render was not what was asked for: {result.error}"

    deliverable = Path(result.data["deliverable"])
    assert deliverable.exists()
    probe = await container.editor.probe(str(deliverable))
    assert (probe.video.width, probe.video.height) == (1080, 1080)

    # Audited, and the chain still verifies afterwards.
    assert len(container.audit.entries()) > before
    assert container.audit.verify_chain()
    logged = [e for e in container.audit.entries() if e.action == "media.edit"]
    assert logged, "an edit that is not in the audit log is an edit nobody can account for"

    # And the originals are untouched, which is what makes the undo safe.
    assert all(Path(image).exists() for image in images)


@needs_ffmpeg
async def test_probing_is_a_read_and_answers_from_the_file(tmp_path):
    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
            log_level="WARNING",
            llm_backend="rule",
            vault_backend="memory",
        ),
        configure_logs=False,
    )
    clip = tmp_path / "var" / "media" / "clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        container.editor.ffmpeg,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=30:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(clip),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.communicate()

    result = await container.executor.execute(
        ToolCall(tool="media.probe", args={"path": str(clip)}, reason="what is this"),
        agent="media",
    )
    assert result.ok and result.verified
    assert result.data["seconds"] == pytest.approx(2.0, abs=0.2)
    assert "320×240" in result.data["summary"]


# ---------------------------------------------------------------------- narration (V12)


def test_the_container_always_has_a_narrator(container):
    """Present whether or not a real voice is configured. One that cannot speak refuses by
    name when asked, which is more use than an attribute that is None."""
    assert container.narrator is not None
    assert container.narrator.backend


async def test_the_stub_voice_refuses_to_narrate_rather_than_writing_json(container, tmp_path):
    """The default backend describes how Thursday would speak. Written to `line01.wav` that
    is a video with silence where the narration should be, and nothing would have failed."""
    from thursday_media.ports import MediaUnavailable

    with pytest.raises(MediaUnavailable) as raised:
        await container.narrator.narrate(["สวัสดีครับ"], tmp_path / "narration")

    assert "text-stub" in raised.value.message
    assert "THURSDAY_TTS_BACKEND=espeak" in raised.value.message
    assert not (tmp_path / "narration").exists() or not list((tmp_path / "narration").iterdir())


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("espeakng_loader"),
    reason="espeakng-loader not installed",
)
async def test_choosing_espeak_gives_the_container_a_voice_that_speaks(tmp_path):
    """End to end through the real container: a setting, a chain, and real audio out."""
    from thursday_shared.audio import is_wav

    container = build_container(
        Settings(
            data_dir=tmp_path / "var",
            obsidian_vault=tmp_path / "vault",
            database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
            log_level="WARNING",
            llm_backend="rule",
            vault_backend="memory",
            tts_backend="espeak",
            tts_voice="th",
        ),
        configure_logs=False,
    )
    assert "espeak-ng" in container.tts.name

    narration = await container.narrator.narrate(
        ["สวัสดีครับ ทดสอบเสียง", "second line"], tmp_path / "narration"
    )
    assert len(narration.lines) == 2
    for line in narration.lines:
        assert line.seconds > 0.2
        assert is_wav(Path(line.path).read_bytes())
