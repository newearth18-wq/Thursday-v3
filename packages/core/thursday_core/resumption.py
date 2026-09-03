"""What to do with a task the crash interrupted (ADR 0039).

Persisting a task is easy and, on its own, actively harmful. A `RUNNING` row reloaded as
`RUNNING` describes a task that looks alive with nothing driving it: the coroutine executing
it died with the process. The owner sees work in progress that will never progress.

So the question this module answers is not "can we store it" but **what is actually known
about a task that was interrupted**, and the system's own principles answer it:

* A step that completed and was **verified** is done. Its outcome was observed (ADR 0012), and
  re-running it would repeat work that already happened.
* A step that was **running** when the process died is *unknown*. Nobody observed its outcome.
  It may have completed, half-completed, or never started. That is precisely the state
  ACT → VERIFY exists to prevent anyone assuming their way out of.
* A step that never started is safe.

The unknown step decides everything. Whether it may be re-run is a question the permission
vocabulary already answers, and re-asking it here rather than inventing a second rule keeps
one source of truth: read-only, reversible and not external is safe to repeat; `email.send`,
`purchase.make` and `file.delete` are not — §194 forbids silently duplicating an external
communication, and a delete repeated is a delete that may take the restored copy.

Nothing here resumes anything. It reports. Resuming is the owner's decision (ADR 0027), for
two reasons: the unknown step's safety is a judgement about *their* data, and a process that
auto-resumed on boot would, in a crash loop, redo the same dangerous thing on every restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_shared.enums import PermissionLevel, StepKind, TaskState, risk_at_least
from thursday_shared.enums import RiskLevel as Risk

from thursday_core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class StepVerdict:
    """What is known about one step of an interrupted plan."""

    seq: int
    name: str
    #: "done" · "unknown" · "not started"
    state: str
    safe_to_repeat: bool
    reason: str


@dataclass(frozen=True)
class Resumption:
    """What can be said about an interrupted task, and what it would take to continue."""

    task_id: Any
    title: str
    steps: tuple[StepVerdict, ...] = ()
    #: The step to start from, or None when there is nothing left to do.
    resume_from: int | None = None
    #: True when every unfinished step can be repeated without risking a duplicate.
    safe: bool = True
    reason: str = ""

    @property
    def unknown(self) -> tuple[StepVerdict, ...]:
        """Steps that were in flight. These are the ones nobody can vouch for."""
        return tuple(s for s in self.steps if s.state == "unknown")

    def describe(self, language: str = "th") -> str:
        if language == "th":
            if self.safe:
                return f"“{self.title}” ค้างอยู่ ผมทำต่อจากขั้นที่ {self.resume_from} ได้ครับ"
            return f"“{self.title}” ค้างอยู่ครับ แต่{self.reason} — ให้ผมทำต่อไหมครับ"
        if self.safe:
            return f"“{self.title}” was interrupted; I can continue from step {self.resume_from}."
        return f"“{self.title}” was interrupted, but {self.reason}. Shall I continue?"


def action_of(step: Any) -> str:
    """The action a step would perform, as the catalogue names it.

    A device step names it in `args["action"]`; a tool step is named by the tool it calls.
    Anything else has no single action and is judged by its kind.
    """
    if step.kind is StepKind.DEVICE:
        return str(step.args.get("action") or "")
    if step.kind is StepKind.TOOL:
        return str(step.args.get("tool") or step.name or "")
    return ""


def safe_to_repeat(action: str, *, policy: Any = None) -> tuple[bool, str]:
    """Whether repeating this action, having no idea whether it already happened, is safe.

    Asked of the policy table rather than answered with a list of names here. The table
    already knows which actions reach outside this machine and which cannot be undone, and a
    second list would be a second thing to keep in step — this repository has found that bug
    enough times.
    """
    if not action:
        # No named action: an agent or a question. Repeating either costs work, not damage.
        return True, "no external effect"

    from thursday_security.policy import PolicyTable

    table = policy or PolicyTable()
    spec = table.get(action)

    if spec.level >= PermissionLevel.EXTERNAL:
        return False, f"{action} reaches outside this machine and may already have happened"
    if not spec.reversible:
        return False, f"{action} cannot be undone, and it is not known whether it ran"
    if risk_at_least(spec.risk, Risk.HIGH):
        return False, f"{action} is high risk and its outcome was never confirmed"
    return True, f"{action} is reversible and local"


def analyse(task: Any, *, policy: Any = None) -> Resumption:
    """Read an interrupted task and say what is known, and what continuing would require."""
    plan = getattr(task, "plan", None)
    steps = list(getattr(plan, "steps", []) or [])
    if not steps:
        return Resumption(
            task_id=task.id,
            title=task.title,
            resume_from=None,
            safe=True,
            reason="there is no plan to continue",
        )

    verdicts: list[StepVerdict] = []
    resume_from: int | None = None
    unsafe: str = ""

    for step in sorted(steps, key=lambda s: s.seq):
        action = action_of(step)
        repeatable, why = safe_to_repeat(action, policy=policy)

        if step.status is TaskState.COMPLETED:
            # Done and observed. Re-running it would repeat work that already happened.
            verdicts.append(StepVerdict(step.seq, step.name, "done", True, "completed"))
            continue

        if step.status is TaskState.RUNNING:
            # The one that matters. Nobody watched this finish, so nobody can say it did.
            verdicts.append(StepVerdict(step.seq, step.name, "unknown", repeatable, why))
            if resume_from is None:
                resume_from = step.seq
            if not repeatable and not unsafe:
                unsafe = why
            continue

        verdicts.append(StepVerdict(step.seq, step.name, "not started", repeatable, why))
        if resume_from is None:
            resume_from = step.seq

    return Resumption(
        task_id=task.id,
        title=task.title,
        steps=tuple(verdicts),
        resume_from=resume_from,
        safe=not unsafe,
        reason=unsafe or "every remaining step can be repeated safely",
    )


def interrupted(tasks: Any) -> list[Any]:
    """Every task the last run left in flight."""
    return [t for t in tasks.list(limit=500) if t.status is TaskState.INTERRUPTED]
