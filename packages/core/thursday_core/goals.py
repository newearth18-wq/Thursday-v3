"""Goals, and the priority that decides what waits (§41, V10).

    GOAL → MISSION → PROJECT → TASK → ACTION

Four levels above the task, and the reason for them is in the spec's own sentence:
*"Thursday ต้องติดตาม goal ไม่ใช่แค่ task"* — track the goal, not just the task. A system that
knows only about tasks can tell you what it did. It cannot tell you whether any of it
mattered, because "mattered" is a question about something the tasks were *for*.

The practical difference shows up in two places. **Progress** stops being "seven of nine
steps" and becomes "two of five missions"; a goal at 100% task completion and 0% mission
completion is a real and common state, and only the second number is worth reporting.
**Priority** stops being an integer nobody can justify: a task inherits urgency from what it
serves, so "why is this ahead of that" has an answer above the level of whoever typed the
number.

Preemption is where this gets dangerous, and the spec says the important half out loud:
*"แต่ต้อง preserve state"*. Interrupting work to run something more urgent is only
acceptable if the interrupted work can resume — otherwise "higher priority" quietly means
"destroys lower-priority work", and a system that loses an afternoon's progress to a
notification is worse than one that never preempts at all. `PriorityQueue.preempt` moves a
task to PAUSED, which the task state machine defines as resumable *where it stopped*, and
refuses to touch anything it cannot pause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_shared.enums import Priority, TaskState
from thursday_shared.ids import new_id

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Re-exported so callers of this module do not need to reach into `thursday_shared`
#: for the enum that this module's whole API is expressed in.
__all__ = ["Goal", "GoalManager", "Mission", "Priority", "PriorityQueue"]


#: Below this, nothing preempts anything. Two NORMAL tasks do not fight; the second waits.
#: Preemption is for the case where something genuinely cannot wait, and making it common
#: would mean nothing ever finishes.
PREEMPT_THRESHOLD = Priority.HIGH


@dataclass
class Goal:
    """Something the owner wants to be true. Outlives every task under it."""

    id: UUID = field(default_factory=new_id)
    title: str = ""
    why: str = ""
    priority: Priority = Priority.NORMAL
    #: Missions are the goal broken into things that can be finished. Held by id so a
    #: mission can be reassigned between goals without rewriting either.
    mission_ids: list[UUID] = field(default_factory=list)
    due: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    achieved_at: datetime | None = None

    @property
    def open(self) -> bool:
        return self.achieved_at is None


@dataclass
class Mission:
    """A finishable piece of a goal. Where projects and tasks hang."""

    id: UUID = field(default_factory=new_id)
    goal_id: UUID | None = None
    title: str = ""
    project_ids: list[UUID] = field(default_factory=list)
    done: bool = False


class GoalManager:
    """The layers above a task, and the arithmetic that makes them worth having."""

    def __init__(self) -> None:
        self._goals: dict[UUID, Goal] = {}
        self._missions: dict[UUID, Mission] = {}

    # ------------------------------------------------------------------ structure

    def add_goal(
        self,
        title: str,
        *,
        why: str = "",
        priority: Priority = Priority.NORMAL,
        due: datetime | None = None,
    ) -> Goal:
        goal = Goal(title=title, why=why, priority=priority, due=due)
        self._goals[goal.id] = goal
        log.info("goal_added", title=title, priority=str(priority))
        return goal

    def add_mission(self, goal_id: UUID, title: str) -> Mission:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise KeyError(f"no goal {goal_id}")
        mission = Mission(goal_id=goal_id, title=title)
        self._missions[mission.id] = mission
        goal.mission_ids.append(mission.id)
        return mission

    def complete_mission(self, mission_id: UUID) -> Mission:
        mission = self._missions[mission_id]
        mission.done = True
        goal = self._goals.get(mission.goal_id) if mission.goal_id else None
        if goal is not None and self.progress(goal.id) >= 1.0:
            # A goal is achieved when its missions are, not when someone remembers to say
            # so. Deriving it means the two can never disagree.
            goal.achieved_at = datetime.now(UTC)
            log.info("goal_achieved", title=goal.title)
        return mission

    def goals(self, *, open_only: bool = True) -> list[Goal]:
        rows = [g for g in self._goals.values() if g.open or not open_only]
        return sorted(
            rows, key=lambda g: (-int(g.priority), g.due or datetime.max.replace(tzinfo=UTC))
        )

    def missions(self, goal_id: UUID) -> list[Mission]:
        return [self._missions[m] for m in self._goals[goal_id].mission_ids if m in self._missions]

    def get(self, goal_id: UUID) -> Goal | None:
        return self._goals.get(goal_id)

    # ------------------------------------------------------------------ progress

    def progress(self, goal_id: UUID) -> float:
        """Missions done over missions total.

        Deliberately not weighted by task counts. A goal with one enormous mission and nine
        trivial ones is 10% done when the trivial ones are finished, and any weighting that
        made that number look better would be measuring effort rather than progress.
        """
        missions = self.missions(goal_id)
        if not missions:
            return 0.0
        return sum(1 for m in missions if m.done) / len(missions)

    def priority_of(self, goal_id: UUID | None) -> Priority:
        """What a task serving this goal inherits. Unattached work is NORMAL."""
        goal = self._goals.get(goal_id) if goal_id else None
        return goal.priority if goal else Priority.NORMAL


class PriorityQueue:
    """Decides what runs and what waits, and never loses the work it pauses."""

    def __init__(self, tasks: Any, goals: GoalManager | None = None) -> None:
        self._tasks = tasks
        self._goals = goals or GoalManager()

    def priority_of(self, task: Any) -> Priority:
        """A task's own priority, or the one it inherits from its goal.

        Inheritance is what makes the number defensible: "this is HIGH because it serves a
        HIGH goal" is an answer, and "this is 7" is not.
        """
        explicit = getattr(task, "priority", None)
        if isinstance(explicit, Priority):
            return explicit
        return self._goals.priority_of(getattr(task, "goal_id", None))

    def ordering(self) -> list[Any]:
        """Runnable work, most urgent first, ties broken by deadline then age."""
        far_future = datetime.max.replace(tzinfo=UTC)
        return sorted(
            (t for t in self._tasks.list() if not t.status.is_terminal),
            key=lambda t: (
                -int(self.priority_of(t)),
                t.deadline or far_future,
                t.created_at,
            ),
        )

    async def preempt(self, incoming: Any) -> list[Any]:
        """Pause running work that the incoming task outranks, and say what was paused.

        Two rules, both load-bearing:

        **State is preserved.** Paused, not cancelled — `TaskState.PAUSED` is defined as
        resuming where it stopped. Without that, "higher priority" would silently mean
        "destroys lower-priority work", and losing an afternoon's progress to something
        urgent is worse than never preempting at all.

        **A task that cannot be paused is left alone.** The transition table decides, and
        where it refuses, the incoming work waits its turn rather than forcing through. A
        preemption that corrupts the thing it interrupted has not prioritised anything.
        """
        incoming_priority = self.priority_of(incoming)
        if incoming_priority < PREEMPT_THRESHOLD:
            return []

        paused: list[Any] = []
        for task in self._tasks.list(status=TaskState.RUNNING):
            if task.id == getattr(incoming, "id", None):
                continue
            if self.priority_of(task) >= incoming_priority:
                continue
            try:
                await self._tasks.transition(
                    task.id, TaskState.PAUSED, reason=f"preempted by {incoming.title!r}"
                )
            except Exception as exc:
                log.info("preempt_declined", task=task.title, reason=str(exc))
                continue
            paused.append(task)
            log.info(
                "task_preempted",
                paused=task.title,
                by=incoming.title,
                priority=str(incoming_priority),
            )
        return paused

    async def resume(self, task_id: UUID) -> Any:
        """Put a preempted task back to work, from where it stopped."""
        return await self._tasks.transition(task_id, TaskState.RUNNING, reason="resumed")
