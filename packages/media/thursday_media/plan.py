"""Edit plans, and the checkpoints that let a long render survive a restart (§15, V11).

A render is the one kind of work in Thursday that is both slow and perfectly deterministic.
Slow means a crash three minutes in is expensive; deterministic means the work already done
is still good afterwards. That combination is what a checkpoint is for, and the brief names
this exact case: *✓ script ✓ storyboard ✓ voice ✗ rendering — restarting should resume from
rendering when safe.*

"When safe" is the whole design problem, and this module answers it narrowly. A step is
skipped on resume only when the checkpoint says it finished **and** the file it produced is
still on disk at exactly the size that was recorded. Anything else — missing, shorter,
longer, no checkpoint entry — is re-run. A half-written MP4 from a process that was killed
mid-write is a different size than the one that was recorded, so it is re-rendered rather
than handed to the next step, which is the failure this check exists to catch.

Plans are plain data on purpose. They serialise to JSON, which means the same plan can be
written by the planner, stored against a task, inspected by the owner before it runs, and
resumed by a different process than the one that started it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from thursday_core.logging import get_logger

from thursday_media.ports import MediaEditor, MediaUnavailable, OperationFailed, Overlay, preset

log = get_logger(__name__)

#: Every operation a plan may name. A plan carrying anything else is rejected before the
#: first step runs rather than failing partway through — a plan that dies at step six having
#: already written five files is a mess somebody has to clean up by hand.
OPERATIONS: frozenset[str] = frozenset(
    {
        "trim",
        "concat",
        "resize",
        "still",
        "burn_subtitles",
        "dub",
        "normalize_audio",
        "strip_silence",
        "overlay",
        "crossfade",
        "thumbnail",
    }
)


@dataclass(frozen=True)
class EditStep:
    """One operation, its arguments, and the file it is expected to produce.

    `produces` is required rather than derived. A step whose output path is computed
    somewhere else is a step whose output cannot be checked for by a resuming process that
    does not have that computation in front of it.
    """

    id: str
    op: str
    produces: str
    args: dict[str, Any] = field(default_factory=dict)
    #: Human-readable, and shown in the task view. Not a comment: this is what the owner
    #: sees when they ask what Thursday is doing right now.
    label: str = ""

    def describe(self) -> str:
        return self.label or f"{self.op} → {Path(self.produces).name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "op": self.op,
            "produces": self.produces,
            "args": self.args,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EditStep:
        return cls(
            id=str(raw["id"]),
            op=str(raw["op"]),
            produces=str(raw["produces"]),
            args=dict(raw.get("args") or {}),
            label=str(raw.get("label") or ""),
        )


@dataclass
class EditPlan:
    """An ordered list of steps and the directory they write into."""

    name: str
    steps: list[EditStep] = field(default_factory=list)
    #: The artefact the owner asked for. Defaults to the last step's output, which is right
    #: for a linear plan and wrong for one that also emits a thumbnail — hence settable.
    deliverable: str = ""

    def validate(self) -> None:
        """Raise if this plan could not possibly run. Called before the first step."""
        if not self.steps:
            raise OperationFailed("an edit plan with no steps produces nothing")

        seen: set[str] = set()
        for step in self.steps:
            if step.op not in OPERATIONS:
                known = ", ".join(sorted(OPERATIONS))
                raise OperationFailed(f"step {step.id!r}: no such operation {step.op!r} ({known})")
            if step.id in seen:
                raise OperationFailed(f"two steps share the id {step.id!r}")
            seen.add(step.id)
            if not step.produces:
                raise OperationFailed(f"step {step.id!r} does not say what it produces")

        # An edit never writes over its input (ADR 0060). Catching it here catches it for
        # every adapter at once, including one written later by somebody who did not read
        # the port docstring.
        outputs = {s.produces for s in self.steps}
        for step in self.steps:
            for source in _sources(step.args):
                if source in outputs and source == step.produces:
                    raise OperationFailed(
                        f"step {step.id!r} reads and writes {source} — an edit must not "
                        "overwrite its own input"
                    )

    @property
    def final(self) -> str:
        return self.deliverable or (self.steps[-1].produces if self.steps else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "deliverable": self.deliverable,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> EditPlan:
        return cls(
            name=str(raw.get("name") or "edit"),
            deliverable=str(raw.get("deliverable") or ""),
            steps=[EditStep.from_dict(s) for s in raw.get("steps") or []],
        )


def _sources(args: dict[str, Any]) -> list[str]:
    """Every input path named in a step's arguments."""
    found: list[str] = []
    for key in ("src", "a", "b", "image", "narration", "music", "subtitles"):
        value = args.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    sources = args.get("sources")
    if isinstance(sources, list):
        found.extend(str(s) for s in sources if s)
    return found


@dataclass
class StepOutcome:
    step: str
    op: str
    ok: bool
    output: str = ""
    seconds: float = 0.0
    error: str = ""
    #: True when a checkpoint let this step be skipped. Reported separately from `ok`
    #: because "we did this" and "this was already done" are different things to tell
    #: somebody who is waiting.
    resumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "op": self.op,
            "ok": self.ok,
            "output": self.output,
            "seconds": round(self.seconds, 3),
            "error": self.error,
            "resumed": self.resumed,
        }


@dataclass
class PlanReport:
    plan: str
    outcomes: list[StepOutcome] = field(default_factory=list)
    deliverable: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)

    @property
    def resumed(self) -> int:
        return sum(1 for o in self.outcomes if o.resumed)

    @property
    def failed_step(self) -> StepOutcome | None:
        return next((o for o in self.outcomes if not o.ok), None)

    @property
    def seconds(self) -> float:
        return sum(o.seconds for o in self.outcomes)

    def describe(self) -> str:
        """The ✓/✗ list the brief asks for, in one string."""
        marks = " ".join(
            ("✓" if o.ok else "✗") + (f"{o.step}~" if o.resumed else o.step) for o in self.outcomes
        )
        failed = self.failed_step
        if failed:
            return f"{marks} — stopped at {failed.step}: {failed.error}"
        return f"{marks} — {Path(self.deliverable).name or 'no deliverable'}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "ok": self.ok,
            "deliverable": self.deliverable,
            "seconds": round(self.seconds, 3),
            "resumed_steps": self.resumed,
            "steps": [o.to_dict() for o in self.outcomes],
        }


class Checkpoint:
    """What finished, and the evidence that it is still finished.

    Stored next to the working files as JSON. Deliberately not in the database: a render's
    intermediate files live in a working directory, and a checkpoint that outlived the
    directory it describes would point at files that are not there.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}

    @classmethod
    def load(cls, path: Path) -> Checkpoint:
        checkpoint = cls(path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                checkpoint.entries = dict(raw.get("completed") or {})
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                # A corrupt checkpoint means the work is re-done, which is slow and correct.
                # Trusting a half-written one means the work is skipped, which is fast and
                # produces a video with a missing scene.
                log.warning("checkpoint_unreadable", path=str(path), error=str(exc))
                checkpoint.entries = {}
        return checkpoint

    def record(self, step: EditStep, output: Path) -> None:
        try:
            size = output.stat().st_size
        except OSError:
            return
        self.entries[step.id] = {"output": str(output), "size": size, "op": step.op}
        self._save()

    def reusable(self, step: EditStep) -> Path | None:
        """The output of a previous run, if it can be trusted. `None` re-runs the step."""
        entry = self.entries.get(step.id)
        if not entry or entry.get("op") != step.op:
            return None
        output = Path(str(entry.get("output") or ""))
        if str(output) != step.produces or not output.exists():
            return None
        try:
            if output.stat().st_size != int(entry.get("size") or -1):
                # Different size than when it was recorded: a truncated write, or somebody
                # replaced the file. Either way it is not the artefact this plan produced.
                return None
        except OSError:
            return None
        return output

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"completed": self.entries}, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            # A checkpoint that cannot be written costs a resume, not a render. Logged
            # rather than raised, because failing the whole job over it would be worse.
            log.warning("checkpoint_unwritable", path=str(self.path), error=str(exc))


ProgressHook = Callable[[StepOutcome], Awaitable[None]]


async def render(
    plan: EditPlan,
    editor: MediaEditor,
    *,
    checkpoint: Path | None = None,
    on_step: ProgressHook | None = None,
) -> PlanReport:
    """Run a plan, step by step, stopping at the first failure.

    Stops rather than continuing, because every step after a failed one takes the failed
    step's output as its input. Carrying on would turn one clear error into five confusing
    ones.
    """
    plan.validate()
    if not editor.available:
        # The editor's own sentence when it has one, so an owner never sees two different
        # explanations for the same missing binary. `UnavailableEditor` carries the full
        # remedy; the fallback is for any other adapter that reports itself unavailable.
        raise MediaUnavailable(
            getattr(editor, "reason", "")
            or (
                f"{editor.name} cannot edit media on this machine — install ffmpeg, or set "
                "THURSDAY_FFMPEG_PATH to point at it. Nothing was changed."
            )
        )

    marks = Checkpoint.load(checkpoint) if checkpoint else None
    report = PlanReport(plan=plan.name, deliverable=plan.final)

    for step in plan.steps:
        started = time.perf_counter()

        reused = marks.reusable(step) if marks else None
        if reused is not None:
            outcome = StepOutcome(
                step=step.id, op=step.op, ok=True, output=str(reused), resumed=True
            )
            log.info("edit_step_resumed", step=step.id, op=step.op, output=str(reused))
            report.outcomes.append(outcome)
            if on_step:
                await on_step(outcome)
            continue

        try:
            output = await _dispatch(editor, step)
        except (MediaUnavailable, OperationFailed) as exc:
            outcome = StepOutcome(
                step=step.id,
                op=step.op,
                ok=False,
                seconds=time.perf_counter() - started,
                error=exc.message,
            )
            log.warning("edit_step_failed", step=step.id, op=step.op, error=exc.message)
            report.outcomes.append(outcome)
            if on_step:
                await on_step(outcome)
            return report

        outcome = StepOutcome(
            step=step.id,
            op=step.op,
            ok=True,
            output=output,
            seconds=time.perf_counter() - started,
        )
        report.outcomes.append(outcome)
        if marks:
            marks.record(step, Path(output))
        if on_step:
            await on_step(outcome)

    return report


async def _dispatch(editor: MediaEditor, step: EditStep) -> str:
    """Call the one editor method this step names.

    An explicit table rather than `getattr(editor, step.op)`: a plan is data, and data that
    reaches `getattr` on a live object is data that can call any method on it. The plan
    could come from a model, and a model cannot be allowed to name `editor.__init__`.
    """
    args = step.args
    dst = step.produces

    if step.op == "trim":
        return await editor.trim(
            str(args["src"]),
            dst,
            start=float(args.get("start", 0.0)),
            end=None if args.get("end") is None else float(args["end"]),
        )
    if step.op == "concat":
        return await editor.concat([str(s) for s in args["sources"]], dst)
    if step.op == "resize":
        return await editor.resize(str(args["src"]), dst, target=preset(str(args["preset"])))
    if step.op == "still":
        return await editor.still(
            str(args["image"]),
            dst,
            seconds=float(args.get("seconds", 3.0)),
            target=preset(str(args.get("preset", "16:9"))),
        )
    if step.op == "burn_subtitles":
        return await editor.burn_subtitles(str(args["src"]), dst, subtitles=str(args["subtitles"]))
    if step.op == "dub":
        return await editor.dub(
            str(args["src"]),
            dst,
            narration=_optional(args.get("narration")),
            music=_optional(args.get("music")),
            music_gain_db=float(args.get("music_gain_db", -18.0)),
        )
    if step.op == "normalize_audio":
        return await editor.normalize_audio(str(args["src"]), dst)
    if step.op == "strip_silence":
        return await editor.strip_silence(
            str(args["src"]), dst, threshold_db=float(args.get("threshold_db", -45.0))
        )
    if step.op == "overlay":
        return await editor.overlay(
            str(args["src"]), dst, overlays=[_overlay(o) for o in args.get("overlays") or []]
        )
    if step.op == "crossfade":
        return await editor.crossfade(
            str(args["a"]), str(args["b"]), dst, seconds=float(args.get("seconds", 0.5))
        )
    if step.op == "thumbnail":
        return await editor.thumbnail(str(args["src"]), dst, at=float(args.get("at", 0.0)))

    raise OperationFailed(f"no such operation {step.op!r}")


def _optional(value: Any) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _overlay(raw: Any) -> Overlay:
    if isinstance(raw, Overlay):
        return raw
    data = dict(raw or {})
    return Overlay(
        image=str(data["image"]),
        corner=str(data.get("corner", "top-right")),
        margin=int(data.get("margin", 32)),
        opacity=float(data.get("opacity", 1.0)),
        start=float(data.get("start", 0.0)),
        end=None if data.get("end") is None else float(data["end"]),
    )
