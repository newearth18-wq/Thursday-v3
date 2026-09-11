"""Edit plans, and the checkpoint that decides what may be skipped after a restart (V11).

The interesting tests here are the ones about *not* resuming. A checkpoint that skips too
much is worse than no checkpoint at all: it hands the next step a half-written file and the
render finishes successfully with a scene missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from thursday_media.plan import Checkpoint, EditPlan, EditStep, render
from thursday_media.ports import ExportPreset, MediaProbe, MediaUnavailable, OperationFailed


class RecordingEditor:
    """An editor that writes a tiny real file per operation and remembers the calls.

    Real files, not mocks, because the checkpoint's whole job is to look at what is on disk.
    """

    name = "recording"
    available = True

    def __init__(self, *, fail_on: str = "") -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    async def _write(self, op: str, dst: str) -> str:
        self.calls.append(op)
        if self.fail_on == op:
            raise OperationFailed(f"{op} was told to fail")
        path = Path(dst)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{op} output", encoding="utf-8")
        return str(path)

    async def probe(self, path: str) -> MediaProbe:
        return MediaProbe(path=path)

    async def trim(self, src, dst, *, start, end=None):
        return await self._write("trim", dst)

    async def concat(self, sources, dst):
        return await self._write("concat", dst)

    async def resize(self, src, dst, *, target):
        return await self._write("resize", dst)

    async def still(self, image, dst, *, seconds, target):
        return await self._write("still", dst)

    async def burn_subtitles(self, src, dst, *, subtitles):
        return await self._write("burn_subtitles", dst)

    async def dub(self, src, dst, *, narration=None, music=None, music_gain_db=-18.0):
        return await self._write("dub", dst)

    async def normalize_audio(self, src, dst):
        return await self._write("normalize_audio", dst)

    async def strip_silence(self, src, dst, *, threshold_db=-45.0):
        return await self._write("strip_silence", dst)

    async def overlay(self, src, dst, *, overlays):
        return await self._write("overlay", dst)

    async def crossfade(self, a, b, dst, *, seconds=0.5):
        return await self._write("crossfade", dst)

    async def thumbnail(self, src, dst, *, at=0.0):
        return await self._write("thumbnail", dst)

    def health(self):
        return {"backend": "recording", "available": True}


def _plan(tmp_path: Path) -> EditPlan:
    return EditPlan(
        name="promo",
        steps=[
            EditStep("card", "still", str(tmp_path / "card.mp4"), {"image": "a.png", "seconds": 2}),
            EditStep(
                "join",
                "concat",
                str(tmp_path / "joined.mp4"),
                {"sources": [str(tmp_path / "card.mp4"), "b.mp4"]},
            ),
            EditStep(
                "voice",
                "dub",
                str(tmp_path / "voiced.mp4"),
                {"src": str(tmp_path / "joined.mp4"), "narration": "n.wav"},
            ),
            EditStep(
                "out",
                "resize",
                str(tmp_path / "final.mp4"),
                {"src": str(tmp_path / "voiced.mp4"), "preset": "9:16"},
            ),
        ],
    )


# ------------------------------------------------------------------------------ validation


def test_a_plan_with_no_steps_is_rejected():
    with pytest.raises(OperationFailed, match="no steps"):
        EditPlan(name="empty").validate()


def test_an_unknown_operation_is_rejected_before_anything_runs(tmp_path):
    plan = EditPlan(name="p", steps=[EditStep("x", "deepfake", str(tmp_path / "o.mp4"))])
    with pytest.raises(OperationFailed, match="no such operation"):
        plan.validate()


def test_duplicate_step_ids_are_rejected(tmp_path):
    plan = EditPlan(
        name="p",
        steps=[
            EditStep("same", "trim", str(tmp_path / "a.mp4"), {"src": "in.mp4"}),
            EditStep("same", "trim", str(tmp_path / "b.mp4"), {"src": "in.mp4"}),
        ],
    )
    with pytest.raises(OperationFailed, match="share the id"):
        plan.validate()


def test_a_step_that_reads_and_writes_the_same_file_is_rejected(tmp_path):
    same = str(tmp_path / "clip.mp4")
    plan = EditPlan(name="p", steps=[EditStep("x", "trim", same, {"src": same})])
    with pytest.raises(OperationFailed, match="overwrite its own input"):
        plan.validate()


def test_a_plan_round_trips_through_json(tmp_path):
    """Plans are stored against a task and resumed by a different process than wrote them."""
    plan = _plan(tmp_path)
    restored = EditPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert [s.id for s in restored.steps] == [s.id for s in plan.steps]
    assert restored.steps[1].args["sources"] == plan.steps[1].args["sources"]
    assert restored.final == plan.final


# --------------------------------------------------------------------------------- running


async def test_a_plan_runs_every_step_in_order(tmp_path):
    editor = RecordingEditor()
    report = await render(_plan(tmp_path), editor)
    assert report.ok
    assert editor.calls == ["still", "concat", "dub", "resize"]
    assert report.deliverable == str(tmp_path / "final.mp4")
    assert report.describe().startswith("✓card ✓join ✓voice ✓out")


async def test_a_failed_step_stops_the_plan_rather_than_poisoning_the_rest(tmp_path):
    """Every step after a failure takes the failed step's output as its input. Carrying on
    turns one clear error into several confusing ones."""
    editor = RecordingEditor(fail_on="dub")
    report = await render(_plan(tmp_path), editor)

    assert not report.ok
    assert editor.calls == ["still", "concat", "dub"]
    assert report.failed_step is not None
    assert report.failed_step.step == "voice"
    assert "✗voice" in report.describe()
    assert not Path(tmp_path / "final.mp4").exists()


async def test_an_unavailable_editor_refuses_before_the_first_step(tmp_path):
    """And with *the editor's own* sentence. Two different explanations for one missing
    binary is how an owner ends up searching for the wrong remedy — the first version of
    this raised "unavailable cannot edit media on this machine", using the adapter's name
    as a noun and dropping the reassurance that nothing had been written."""
    from thursday_media.unavailable import REASON, UnavailableEditor

    with pytest.raises(MediaUnavailable) as raised:
        await render(_plan(tmp_path), UnavailableEditor())
    assert raised.value.message == REASON
    assert "Nothing was changed" in raised.value.message
    assert not (tmp_path / "card.mp4").exists()


async def test_progress_is_reported_step_by_step(tmp_path):
    seen = []

    async def hook(outcome):
        seen.append((outcome.step, outcome.ok))

    await render(_plan(tmp_path), RecordingEditor(), on_step=hook)
    assert seen == [("card", True), ("join", True), ("voice", True), ("out", True)]


async def test_a_model_cannot_name_a_method_that_is_not_an_operation(tmp_path):
    """Dispatch is an explicit table, not `getattr(editor, step.op)`. A plan is data, it can
    come from a model, and data that reaches getattr on a live object can call anything."""
    plan = EditPlan(name="p", steps=[EditStep("x", "__init__", str(tmp_path / "o.mp4"))])
    with pytest.raises(OperationFailed, match="no such operation"):
        plan.validate()


# ----------------------------------------------------------------------------- checkpoints


async def test_a_restart_resumes_from_where_it_stopped(tmp_path):
    """The case the brief names: ✓ script ✓ storyboard ✓ voice ✗ rendering."""
    marks = tmp_path / "checkpoint.json"
    first = RecordingEditor(fail_on="resize")
    interrupted = await render(_plan(tmp_path), first, checkpoint=marks)
    assert not interrupted.ok
    assert first.calls == ["still", "concat", "dub", "resize"]

    second = RecordingEditor()
    resumed = await render(_plan(tmp_path), second, checkpoint=marks)

    assert resumed.ok
    # Only the step that failed is done again.
    assert second.calls == ["resize"]
    assert resumed.resumed == 3
    assert "~" in resumed.describe()


async def test_a_truncated_output_is_re_rendered_rather_than_trusted(tmp_path):
    """A process killed mid-write leaves a file that exists and is the wrong length. Using
    it would produce a video with a broken scene and no error anywhere."""
    marks = tmp_path / "checkpoint.json"
    await render(_plan(tmp_path), RecordingEditor(), checkpoint=marks)

    truncated = tmp_path / "joined.mp4"
    truncated.write_text("half", encoding="utf-8")

    editor = RecordingEditor()
    report = await render(_plan(tmp_path), editor, checkpoint=marks)

    assert report.ok
    assert "concat" in editor.calls, "a file of the wrong size must not be resumed from"
    assert "still" not in editor.calls, "the steps that are still intact should be kept"


async def test_a_deleted_output_is_re_rendered(tmp_path):
    marks = tmp_path / "checkpoint.json"
    await render(_plan(tmp_path), RecordingEditor(), checkpoint=marks)
    (tmp_path / "voiced.mp4").unlink()

    editor = RecordingEditor()
    await render(_plan(tmp_path), editor, checkpoint=marks)
    assert "dub" in editor.calls


async def test_a_corrupt_checkpoint_re_renders_everything(tmp_path):
    """Slow and correct beats fast and wrong."""
    marks = tmp_path / "checkpoint.json"
    await render(_plan(tmp_path), RecordingEditor(), checkpoint=marks)
    marks.write_text("{not json", encoding="utf-8")

    editor = RecordingEditor()
    await render(_plan(tmp_path), editor, checkpoint=marks)
    assert editor.calls == ["still", "concat", "dub", "resize"]


async def test_changing_a_step_invalidates_its_checkpoint(tmp_path):
    """The step id is the same and the operation is different — that is an edited plan, and
    the old output does not answer it."""
    marks = tmp_path / "checkpoint.json"
    await render(_plan(tmp_path), RecordingEditor(), checkpoint=marks)

    changed = _plan(tmp_path)
    changed.steps[0] = EditStep("card", "trim", str(tmp_path / "card.mp4"), {"src": "in.mp4"})
    editor = RecordingEditor()
    await render(changed, editor, checkpoint=marks)
    assert editor.calls[0] == "trim"


def test_a_checkpoint_that_cannot_be_written_does_not_fail_the_render(tmp_path):
    """Losing a checkpoint costs a resume. Failing the job over it costs the render."""
    unwritable = tmp_path / "file.txt"
    unwritable.write_text("i am not a directory", encoding="utf-8")
    checkpoint = Checkpoint(unwritable / "nested" / "checkpoint.json")
    produced = tmp_path / "out.mp4"
    produced.write_text("x", encoding="utf-8")
    checkpoint.record(EditStep("a", "trim", str(produced)), produced)  # must not raise


def test_presets_resolve_from_what_a_person_would_say():
    from thursday_media.ports import preset

    assert preset("9:16").height == 1920
    assert preset("tiktok").is_portrait
    assert preset("YouTube").aspect == "16:9"
    assert preset("square").width == preset("square").height
    with pytest.raises(OperationFailed, match="no export preset"):
        preset("4:3")


def test_a_preset_reports_its_own_aspect():
    assert ExportPreset("odd", 1440, 1080).aspect == "4:3"
