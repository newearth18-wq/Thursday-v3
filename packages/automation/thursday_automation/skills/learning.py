"""Learning a skill by watching (§51, V9).

`SkillRegistry.capture` takes a list of steps, and until now nothing produced one: skills
could be written down but not *learned*. This is the observer that produces them.

It is a different thing from `RoutineLearner`, which sits next door and looks similar. That
one finds habits — "you tend to run these tools around nine". This one finds **workflows**:
the same operations, in the same order, done again. Order is the whole difference. "Open the
spreadsheet, filter it, then write the report" and "write the report, filter it, then open
the spreadsheet" contain identical tools and only one of them is a thing anybody does.

Four rules shape what gets proposed, and each exists because the alternative produces
something worse than nothing:

**Only sequences that worked.** A run where a step failed or could not be verified teaches a
workflow that reliably does not work. Learning it would mean offering the owner a skill whose
first outing repeats their bad afternoon.

**Only sequences done more than once.** One occurrence is an event. `MIN_REPEATS` distinct
runs is the smallest number that can distinguish a routine from a Tuesday.

**Arguments that vary become parameters.** Where the same step ran with a different path each
time, the path is an input, not part of the workflow — which is exactly the information that
turns a recording into something reusable, and it is free: it falls out of comparing the runs.

**The result is a draft, always.** It has been watched, not reviewed. Everything the skill
lifecycle does — sandbox, approval, the refusal to activate anything destructive unattended
— exists precisely for a workflow nobody wrote, and a learner that produced active skills
would walk around all of it (§52, ADR 0026).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger

from thursday_automation.skills.models import SkillStep

log = get_logger(__name__)

#: How many separate runs of the same ordered sequence before it is worth proposing.
#: Two is the smallest number that can tell a routine from a one-off, and the cost of being
#: wrong is a question the owner declines rather than an action they have to undo.
MIN_REPEATS = 2

#: Shorter than this is not a workflow. A single tool call is a command, and proposing to
#: "learn" it would bury the real proposals under noise.
MIN_STEPS = 2

#: Longest sequence considered. A very long run is usually several jobs that happened to
#: share a task, and proposing it as one skill would be proposing something nobody asked for.
MAX_STEPS = 12

#: How many completed runs to remember. Bounded: an assistant that has been running for a
#: month must not be holding a month of tool calls to answer a question about last week.
MAX_RUNS = 200


@dataclass
class ObservedStep:
    """One thing that happened: a tool call, or a job an agent did.

    Both, because a skill step is either (`SkillStep`), and a workflow made only of the
    tool calls would be missing half of itself. "Read the file, analyse it, write the
    report" is three steps and only the first is a tool — watching tools alone sees a
    one-step workflow and proposes nothing.
    """

    tool: str = ""
    agent: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    verified: bool = True

    @property
    def name(self) -> str:
        return self.tool or self.agent


@dataclass
class ObservedRun:
    """One task's worth of work, in the order it happened."""

    task_id: UUID | None
    steps: list[ObservedStep] = field(default_factory=list)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def signature(self) -> tuple[str, ...]:
        """What makes two runs "the same workflow": the tools, in order."""
        return tuple(step.name for step in self.steps)

    @property
    def clean(self) -> bool:
        """Whether every step worked *and* was confirmed to have worked."""
        return bool(self.steps) and all(s.ok and s.verified for s in self.steps)


@dataclass(frozen=True)
class SkillProposal:
    """A workflow worth offering to keep. Not a skill until the owner says so."""

    signature: tuple[str, ...]
    steps: list[SkillStep]
    runs: int
    #: Argument names that differed between runs — the workflow's inputs.
    parameters: tuple[str, ...]

    def describe(self, language: str = "th") -> str:
        tools = " → ".join(self.signature)
        if language == "th":
            return f"ผมเห็นคุณทำแบบนี้ {self.runs} ครั้ง: {tools} ต้องการให้ผมจำไว้เป็นสกิลไหมครับ"
        return (
            f"I have seen you do this {self.runs} times: {tools}. "
            "Would you like me to keep it as a skill?"
        )


class SkillObserver:
    """Watches executed work and notices workflows worth keeping."""

    def __init__(self, *, min_repeats: int = MIN_REPEATS, max_runs: int = MAX_RUNS) -> None:
        self._open: dict[UUID | None, ObservedRun] = {}
        self._runs: list[ObservedRun] = []
        self._min_repeats = min_repeats
        self._max_runs = max_runs
        self._proposed: set[tuple[str, ...]] = set()

    def attach(self, bus: object) -> None:
        # Plan steps, not raw tool calls. A plan step *is* a skill step — the two models
        # line up one to one — while a tool call is one layer below and misses every agent
        # job. Subscribing to both would double-count the device actions, which appear as
        # a plan step and again as the tool call inside it.
        bus.subscribe("task.step.completed", self.on_step)  # type: ignore[attr-defined]
        # Every terminal state, not just success: an open run that is never closed is a
        # slow leak, and a cancelled run is one the observer must forget rather than keep
        # half of.
        for state in ("completed", "failed", "cancelled"):
            bus.subscribe(f"task.{state}", self.on_task_finished)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ collecting

    async def on_step(self, event: Any) -> None:
        """Record one completed plan step against its task's run."""
        payload = event.payload
        action = payload.get("action")
        agent = str(payload.get("agent") or payload.get("step") or "")
        if not action and not agent:
            return
        run = self._open.setdefault(event.task_id, ObservedRun(task_id=event.task_id))
        run.steps.append(
            ObservedStep(
                # A device action is recorded as the tool it is; anything else as the agent
                # that did it. That is exactly the tool/agent split `SkillStep` requires.
                tool=str(action) if action else "",
                agent="" if action else agent,
                # Already redacted by the publisher. The observer never sees a secret, so a
                # learned skill cannot carry one.
                args=dict(payload.get("args") or {}),
                ok=True,
                verified=bool(payload.get("verified", True)),
            )
        )

    async def on_task_finished(self, event: Any) -> None:
        """Close a run when its task ends. An open run is still being written."""
        run = self._open.pop(event.task_id, None)
        if run is None or not run.clean:
            # A failed or unverified run is dropped rather than remembered. Learning it
            # would mean offering the owner a workflow whose first outing repeats their
            # bad afternoon.
            return
        if not (MIN_STEPS <= len(run.steps) <= MAX_STEPS):
            return
        self._runs.append(run)
        del self._runs[: max(0, len(self._runs) - self._max_runs)]

    # ------------------------------------------------------------------ proposing

    def proposals(self) -> list[SkillProposal]:
        """Workflows seen often enough to be worth offering, most-repeated first."""
        by_signature: dict[tuple[str, ...], list[ObservedRun]] = defaultdict(list)
        for run in self._runs:
            by_signature[run.signature].append(run)

        out: list[SkillProposal] = []
        for signature, runs in by_signature.items():
            if len(runs) < self._min_repeats:
                continue
            steps, parameters = self._generalise(runs)
            out.append(
                SkillProposal(
                    signature=signature, steps=steps, runs=len(runs), parameters=parameters
                )
            )
        out.sort(key=lambda p: p.runs, reverse=True)
        return out

    def unproposed(self) -> list[SkillProposal]:
        return [p for p in self.proposals() if p.signature not in self._proposed]

    def mark_proposed(self, proposal: SkillProposal) -> None:
        """Asked once. An assistant that keeps asking is one people stop reading."""
        self._proposed.add(proposal.signature)

    # ------------------------------------------------------------------ internals

    def _generalise(self, runs: list[ObservedRun]) -> tuple[list[SkillStep], tuple[str, ...]]:
        """Turn several runs of the same sequence into one parameterised workflow.

        An argument that held the same value every time is part of the workflow. One that
        changed is an *input* to it, and is left out of the captured step so the caller
        supplies it — which is what makes the difference between a recording and something
        reusable. It costs nothing to work out: it is visible in the runs themselves.
        """
        length = len(runs[0].steps)
        steps: list[SkillStep] = []
        parameters: set[str] = set()

        for index in range(length):
            variants = [run.steps[index] for run in runs]
            fixed: dict[str, Any] = {}
            for key in variants[0].args:
                values = [v.args.get(key) for v in variants]
                if all(value == values[0] for value in values):
                    fixed[key] = values[0]
                else:
                    parameters.add(key)
            steps.append(
                SkillStep(
                    seq=index,
                    tool=variants[0].tool,
                    agent=variants[0].agent,
                    args=fixed,
                    condition=f"observed {len(runs)} times",
                )
            )

        log.debug(
            "skill_generalised",
            steps=len(steps),
            runs=len(runs),
            parameters=sorted(parameters),
        )
        return steps, tuple(sorted(parameters))

    def __len__(self) -> int:
        return len(self._runs)
