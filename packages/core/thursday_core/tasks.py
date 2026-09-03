"""Task system (§41–43).

Tasks are core-side objects with UUIDs so a device or client can die mid-task and the work
survives — that is what makes cross-device continuity (§23) possible at all. Transitions are
validated against the state machine rather than assigned freely.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from thursday_shared.enums import TASK_TRANSITIONS, Priority, TaskState
from thursday_shared.errors import BudgetExceeded, ThursdayError
from thursday_shared.models import Budget, Event, Plan, Spend, Task, VerificationReport

from thursday_core.logging import get_logger

log = get_logger(__name__)


class InvalidTransition(ThursdayError):
    code = "invalid_task_transition"


class TaskManager:
    """Owns task lifecycle and the (in-memory here, Postgres in production) task table."""

    def __init__(self, bus: object | None = None, *, repository: Any = None) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._bus = bus
        self._cancelled: set[UUID] = set()
        #: Where tasks live between runs. `_tasks` is an index over it, written through on
        #: every mutation and loaded at startup (ADR 0036).
        from thursday_core.persistence import NullRepository

        self._repository = repository or NullRepository()

    # ------------------------------------------------------------------ lifecycle

    async def create(
        self,
        *,
        title: str,
        objective: str,
        session_id: UUID | None = None,
        project_id: UUID | None = None,
        parent_task_id: UUID | None = None,
        origin_device_id: UUID | None = None,
        budget: Budget | None = None,
        priority: Priority = Priority.NORMAL,
        deadline: datetime | None = None,
    ) -> Task:
        parent = self._tasks.get(parent_task_id) if parent_task_id else None
        effective_budget = budget or Budget()
        if parent is not None:
            # A subtask can never be given more room than its parent has left.
            effective_budget = effective_budget.intersect(parent.budget)
        task = Task(
            title=title,
            objective=objective,
            session_id=session_id,
            project_id=project_id or (parent.project_id if parent else None),
            parent_task_id=parent_task_id,
            origin_device_id=origin_device_id,
            budget=effective_budget,
            priority=priority,
            deadline=deadline,
        )
        self._tasks[task.id] = task
        await self._save(task)
        await self._emit("task.created", task)
        return task

    def get(self, task_id: UUID) -> Task | None:
        return self._tasks.get(task_id)

    def list(
        self, *, status: TaskState | None = None, project_id: UUID | None = None, limit: int = 50
    ) -> list[Task]:
        rows = list(self._tasks.values())
        if status is not None:
            rows = [t for t in rows if t.status is status]
        if project_id is not None:
            rows = [t for t in rows if t.project_id == project_id]
        return sorted(rows, key=lambda t: t.created_at, reverse=True)[:limit]

    async def transition(self, task_id: UUID, to: TaskState, *, reason: str = "") -> Task:
        task = self._require(task_id)
        if to is task.status:
            return task
        allowed = TASK_TRANSITIONS[task.status]
        if to not in allowed:
            raise InvalidTransition(
                f"cannot move task from {task.status} to {to}",
                task_id=str(task_id),
                allowed=sorted(allowed),
            )
        task.status = to
        task.updated_at = datetime.now(UTC)
        if to is TaskState.RUNNING and task.started_at is None:
            task.started_at = task.updated_at
        if to.is_terminal:
            task.finished_at = task.updated_at
            if to is TaskState.COMPLETED:
                task.progress = 1.0
        await self._save(task)
        await self._emit(f"task.{to.value.lower()}", task, reason=reason)
        return task

    async def set_plan(self, task_id: UUID, plan: Plan) -> Task:
        task = self._require(task_id)
        task.plan = plan
        task.updated_at = datetime.now(UTC)
        await self._save(task)
        return task

    async def complete(
        self, task_id: UUID, *, result: dict, verification: VerificationReport
    ) -> Task:
        """Completion requires a passing verification. This is §76 in code."""
        task = self._require(task_id)
        if not verification.passed:
            raise ThursdayError(
                "refusing to complete a task whose verification did not pass",
                task_id=str(task_id),
                verdict=verification.verdict,
            )
        task.result = result
        task.verification = verification
        return await self.transition(task_id, TaskState.COMPLETED)

    async def fail(
        self, task_id: UUID, error: str, *, verification: VerificationReport | None = None
    ) -> Task:
        task = self._require(task_id)
        task.error = error
        if verification is not None:
            task.verification = verification
        return await self.transition(task_id, TaskState.FAILED, reason=error)

    async def pause(self, task_id: UUID, *, reason: str = "paused by the owner") -> Task:
        """PART 5. A paused task keeps its plan and its progress; it resumes where it stopped."""
        return await self.transition(task_id, TaskState.PAUSED, reason=reason)

    async def resume(self, task_id: UUID) -> Task:
        """Back to RUNNING if it had started, READY if it had not."""
        task = self._require(task_id)
        if task.status is not TaskState.PAUSED:
            raise InvalidTransition(
                f"only a paused task can be resumed; this one is {task.status}",
                task_id=str(task_id),
            )
        target = TaskState.RUNNING if task.started_at else TaskState.READY
        return await self.transition(task_id, target, reason="resumed by the owner")

    async def mark_ready(self, task_id: UUID) -> Task:
        """Planned and authorised, waiting for a worker. This is what a queue schedules."""
        return await self.transition(task_id, TaskState.READY)

    async def cancel(self, task_id: UUID, *, reason: str = "cancelled by user") -> Task:
        self._cancelled.add(task_id)
        return await self.transition(task_id, TaskState.CANCELLED, reason=reason)

    def is_cancelled(self, task_id: UUID) -> bool:
        return task_id in self._cancelled

    async def update_progress(self, task_id: UUID, progress: float) -> Task:
        task = self._require(task_id)
        task.progress = max(0.0, min(1.0, progress))
        task.updated_at = datetime.now(UTC)
        await self._save(task)
        await self._emit("task.progress", task)
        return task

    # ------------------------------------------------------------------ budget (§61)

    async def charge(self, task_id: UUID, spend: Spend) -> None:
        task = self._require(task_id)
        for field in ("tokens", "usd", "seconds", "agent_calls", "tool_calls"):
            setattr(task.spent, field, getattr(task.spent, field) + getattr(spend, field))
        await self._save(task)
        if breach := task.spent.exceeds(task.budget):
            raise BudgetExceeded(
                f"task exceeded its {breach} budget",
                task_id=str(task_id),
                limit=getattr(task.budget, breach),
                spent=getattr(task.spent, breach),
            )

    # ------------------------------------------------------------------ backup (Sprint 47)

    def export_state(self) -> list[dict]:
        return [task.model_dump(mode="json") for task in self._tasks.values()]

    def import_state(self, rows: list[dict], *, replace: bool = True) -> int:
        """Restore tasks exactly as they were, terminal states included.

        No transition validation on the way in. These states were already reached legally,
        and re-validating a restore would refuse a task that is legitimately mid-flight —
        making the backup useless for the case it exists for.
        """
        if replace:
            self._tasks.clear()
        for row in rows:
            task = Task.model_validate(row)
            self._tasks[task.id] = task
        return len(rows)

    # ------------------------------------------------------------------ persistence

    async def _save(self, task: Task) -> None:
        """Write a task through to storage.

        Called from `transition` — which every state change funnels through — and from the
        three mutators that change fields without changing state. A test enumerates the
        public API and asserts each mutator persists, because "remember to call this" is how
        the next mutator silently does not.
        """
        await self._repository.put(task.model_dump(mode="python"))

    async def restore(self) -> int:
        """Load tasks, and tell the truth about the ones that were running.

        A `RUNNING` row comes back as `INTERRUPTED`, never as `RUNNING`. The coroutine that
        was executing it died with the process, so a task reloaded as running is a task that
        looks alive with nothing driving it — which is worse than losing it, because the
        owner watches it not progress and has no reason to think anything is wrong.
        """
        rows = await self._repository.load()
        restored = 0
        interrupted = 0
        for row in rows:
            try:
                task = Task.model_validate(row)
            except ValidationError as exc:
                log.warning("task_row_unreadable", error=str(exc))
                continue
            if task.status is TaskState.RUNNING:
                task.status = TaskState.INTERRUPTED
                interrupted += 1
            self._tasks[task.id] = task
            restored += 1

        if restored:
            log.info("tasks_restored", tasks=restored, interrupted=interrupted)
        return restored

    def _require(self, task_id: UUID) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise ThursdayError("unknown task", task_id=str(task_id))
        return task

    async def _emit(self, kind: str, task: Task, **payload: object) -> None:
        if self._bus is None:
            return
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                kind=kind,
                task_id=task.id,
                session_id=task.session_id,
                device_id=task.origin_device_id,
                payload={"title": task.title, "status": task.status, **payload},
            )
        )


class TaskQueue:
    """Long work runs here so the conversation is never blocked (§43)."""

    def __init__(self, *, concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._running: dict[UUID, asyncio.Task[object]] = {}

    async def submit(self, task_id: UUID, coro: object) -> asyncio.Task[object]:
        async def runner() -> object:
            async with self._semaphore:
                return await coro  # type: ignore[misc]

        handle = asyncio.create_task(runner(), name=f"task:{task_id}")
        self._running[task_id] = handle
        handle.add_done_callback(lambda _: self._running.pop(task_id, None))
        return handle

    def cancel(self, task_id: UUID) -> bool:
        handle = self._running.get(task_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    def running(self) -> list[UUID]:
        return list(self._running)

    async def drain(self) -> None:
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)
