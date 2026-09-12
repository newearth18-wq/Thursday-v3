"""Media editing as registered tools (§16, V11).

Editing reaches the machine the same way every other capability does: through the Tool
Registry, gated by the Permission Engine, audited, and verified. Nothing in this package is
callable by an agent except through here — which is what stops a plan produced by a model
from being a plan that runs unchecked.

**The unit of work is a plan, not an operation.** One `media.edit` call renders a whole
`EditPlan`. Registering twelve operation-level tools instead would mean twelve permission
decisions for one video, and approval fatigue is a safety failure of its own (§21).

`verified` on the result is the quality gate's verdict, not ffmpeg's exit code. A render
that produced a file of the wrong shape, with no audio where narration was asked for, or
with subtitles running past the end of the picture comes back `ok=True, verified=False` —
work was done, and it is not what was asked for. The Supervisor treats that as a failure to
report, which is the whole point.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from thursday_shared.enums import ControlTier, PermissionLevel, RiskLevel
from thursday_shared.models import ToolCall, ToolResult, ToolSpec, UndoRecord

from thursday_media.plan import EditPlan, render
from thursday_media.ports import MediaUnavailable, OperationFailed, preset
from thursday_media.quality import check_output
from thursday_media.subtitles import SubtitleTrack, parse_srt


class MediaProbeTool:
    """Read what is in a media file. The one media tool that changes nothing."""

    spec = ToolSpec(
        name="media.probe",
        description="Read the duration, dimensions, codecs and streams of a media file.",
        capabilities=["media", "inspect", "probe"],
        permission=PermissionLevel.READ,
        risk=RiskLevel.NONE,
        latency_ms=120,
        # A photograph never leaves the machine to be measured, and no model is involved.
        local_only=True,
        input_schema={"path": "string"},
        output_schema={"seconds": "float?", "streams": "list", "summary": "string"},
    )

    def __init__(self, editor: Any) -> None:
        self._editor = editor

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        path = str(call.args.get("path") or "")
        if not path:
            return _failed(call, self.spec.name, "no file was named", started)
        try:
            probe = await self._editor.probe(path)
        except (MediaUnavailable, OperationFailed) as exc:
            return _failed(call, self.spec.name, exc.message, started)

        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            # Read off the file itself, which is the only evidence there is for what a file
            # contains.
            verified=True,
            data={
                "path": probe.path,
                "seconds": probe.seconds,
                "container": probe.container,
                "size_bytes": probe.size_bytes,
                "summary": probe.describe(),
                "streams": [
                    {
                        "kind": s.kind,
                        "codec": s.codec,
                        "width": s.width,
                        "height": s.height,
                        "fps": s.fps,
                        "channels": s.channels,
                    }
                    for s in probe.streams
                ],
            },
            evidence={"summary": probe.describe()},
            duration_ms=(time.perf_counter() - started) * 1000,
        )


class MediaEditTool:
    """Render an edit plan, then check the result against what was asked for."""

    spec = ToolSpec(
        name="media.edit",
        description=(
            "Render a media edit plan — trim, join, resize, subtitle, dub, overlay — "
            "writing new files and never changing the originals."
        ),
        capabilities=["media", "video", "audio", "edit", "render", "subtitle"],
        # It writes files. It never overwrites one, which is why the risk is LOW rather
        # than HIGH and why this does not sit alongside file.delete.
        permission=PermissionLevel.MODIFY,
        control_tier=ControlTier.API,
        risk=RiskLevel.LOW,
        latency_ms=30_000,
        local_only=True,
        input_schema={
            "plan": "dict",
            "checkpoint": "string?",
            "expect": "dict?",
        },
        output_schema={
            "deliverable": "string",
            "steps": "list",
            "quality": "dict",
            "summary": "string",
        },
        # Every output is a file this call created, so reversing it is deleting them. No
        # original is ever touched, so there is no previous state to restore.
        reversible=True,
        supports_undo=True,
    )

    def __init__(self, editor: Any, *, workdir: Path | None = None) -> None:
        self._editor = editor
        self._workdir = workdir

    async def run(self, call: ToolCall, ctx: Any) -> ToolResult:
        started = time.perf_counter()
        raw = call.args.get("plan")
        if not isinstance(raw, dict):
            return _failed(call, self.spec.name, "no edit plan was given", started)

        try:
            plan = EditPlan.from_dict(raw)
            plan.validate()
        except (OperationFailed, KeyError, TypeError, ValueError) as exc:
            return _failed(call, self.spec.name, _message(exc), started)

        checkpoint = call.args.get("checkpoint")
        try:
            report = await render(
                plan,
                self._editor,
                checkpoint=Path(str(checkpoint)) if checkpoint else None,
            )
        except MediaUnavailable as exc:
            # The "not installed" path. Nothing ran, nothing was written, and the message
            # names the remedy.
            return _failed(call, self.spec.name, exc.message, started)
        except OperationFailed as exc:
            return _failed(call, self.spec.name, exc.message, started)

        data: dict[str, Any] = {
            "deliverable": report.deliverable if report.ok else "",
            "steps": [o.to_dict() for o in report.outcomes],
            "resumed_steps": report.resumed,
            "quality": {},
            "summary": report.describe(),
        }

        if not report.ok:
            failure = report.failed_step
            return ToolResult(
                call_id=call.id,
                tool=self.spec.name,
                ok=False,
                verified=False,
                data=data,
                error=failure.error if failure else "the edit plan did not finish",
                duration_ms=(time.perf_counter() - started) * 1000,
            )

        quality = await self._inspect(report.deliverable, call.args.get("expect"))
        data["quality"] = quality.to_dict()
        data["summary"] = f"{report.describe()} — {quality.describe()}"

        created = [o.output for o in report.outcomes if o.output and not o.resumed]
        return ToolResult(
            call_id=call.id,
            tool=self.spec.name,
            ok=True,
            # Not "ffmpeg exited zero". The deliverable was opened and found to hold what
            # was asked for — or it was not, and this says so.
            verified=quality.ok,
            data=data,
            evidence={
                "deliverable": report.deliverable,
                "checks": [f"{c.name}:{c.state}" for c in quality.checks],
            },
            error=None if quality.ok else f"the render finished but {quality.describe()}",
            duration_ms=(time.perf_counter() - started) * 1000,
            undo=UndoRecord(
                action_id=call.id,
                operation="media_edit",
                args={"created": created},
                description=f"delete the {len(created)} file(s) this edit produced",
            ),
        )

    async def _inspect(self, deliverable: str, expect: Any):
        """Probe the deliverable and judge it against the caller's expectations."""
        wanted = dict(expect or {})
        try:
            probe = await self._editor.probe(deliverable)
        except (MediaUnavailable, OperationFailed) as exc:
            from thursday_media.quality import Check, QualityReport

            return QualityReport(
                path=deliverable,
                checks=[Check("not_corrupt", "unknown", f"the output could not be read: {exc}")],
            )

        track: SubtitleTrack | None = None
        sidecar = wanted.get("subtitles")
        if isinstance(sidecar, str) and sidecar:
            try:
                track = parse_srt(
                    await asyncio.to_thread(Path(sidecar).read_text, encoding="utf-8")
                )
            except OSError:
                track = SubtitleTrack(cues=[])

        target = None
        if wanted.get("preset"):
            try:
                target = preset(str(wanted["preset"]))
            except OperationFailed:
                target = None

        return check_output(
            probe,
            target=target,
            expect_audio=bool(wanted.get("audio", False)),
            expect_video=bool(wanted.get("video", True)),
            subtitles=track,
            min_seconds=_number(wanted.get("min_seconds")),
            max_seconds=_number(wanted.get("max_seconds")),
        )


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _message(exc: Exception) -> str:
    return getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"


def _failed(call: ToolCall, tool: str, error: str, started: float) -> ToolResult:
    return ToolResult(
        call_id=call.id,
        tool=tool,
        ok=False,
        verified=False,
        data={},
        error=error,
        duration_ms=(time.perf_counter() - started) * 1000,
    )


def register_media_tools(registry: Any, editor: Any, *, workdir: Path | None = None) -> None:
    """Put the media tools in the registry.

    Registered whether or not ffmpeg is present. An editor that is unavailable refuses each
    call with a reason and a remedy, which is a better answer than a tool that is simply not
    there — "Thursday cannot do that here, install ffmpeg" tells the owner something;
    `ToolNotFound` does not.
    """
    registry.register(MediaProbeTool(editor))
    registry.register(MediaEditTool(editor, workdir=workdir))


async def undo_media_edit(record: UndoRecord) -> bool:
    """Delete the files an edit produced.

    Safe because of the invariant the whole package is built on: an edit only ever writes
    new files, so everything named here was created by the call being undone and no
    original is at risk. A file that is already gone counts as undone.
    """
    created = [Path(str(p)) for p in record.args.get("created") or []]
    removed = await asyncio.to_thread(_delete_all, created)
    return removed == len(created)


def _delete_all(paths: list[Path]) -> int:
    """Delete each path, counting the ones that are gone afterwards.

    A file that was already missing counts as undone — the state the undo was asking for is
    the state it is in.
    """
    removed = 0
    for target in paths:
        try:
            target.unlink(missing_ok=True)
            removed += 1
        except OSError:
            break
    return removed
