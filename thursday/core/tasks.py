"""Task system (§41–43).

Tasks are core-side objects with UUIDs so a device or client can die mid-task and the work
survives — that is what makes cross-device continuity (§23) possible at all. Transitions are
validated against the state machine rather than assigned freely.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from thursday.core.logging import get_logger
from thursday.shared.enums import TASK_TRANSITIONS, TaskState
from thursday.shared.errors import BudgetExceeded, ThursdayError
from thursday.shared.models import Budget, Event, Plan, Spend, Task, VerificationReport

log = get_logger(__name__)


class InvalidTransition(ThursdayError):
    code = "invalid_task_transition"


class TaskManager:
    """Owns task lifecycle and the (in-memory here, Postgres in production) task table."""

    def __init__(self, bus: object | None = None) -> None:
        self._tasks: dict[UUID, Task] = {}
        self._bus = bus
        self._cancelled: set[UUID] = set()

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
        priority: int = 5,
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
        await self._emit(f"task.{to.value.lower()}", task, reason=reason)
        return task

    async def set_plan(self, task_id: UUID, plan: Plan) -> Task:
        task = self._require(task_id)
        task.plan = plan
        task.updated_at = datetime.now(UTC)
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

    async def cancel(self, task_id: UUID, *, reason: str = "cancelled by user") -> Task:
        self._cancelled.add(task_id)
        return await self.transition(task_id, TaskState.CANCELLED, reason=reason)

    def is_cancelled(self, task_id: UUID) -> bool:
        return task_id in self._cancelled

    async def update_progress(self, task_id: UUID, progress: float) -> Task:
        task = self._require(task_id)
        task.progress = max(0.0, min(1.0, progress))
        task.updated_at = datetime.now(UTC)
        await self._emit("task.progress", task)
        return task

    # ------------------------------------------------------------------ budget (§61)

    def charge(self, task_id: UUID, spend: Spend) -> None:
        task = self._require(task_id)
        for field in ("tokens", "usd", "seconds", "agent_calls", "tool_calls"):
            setattr(task.spent, field, getattr(task.spent, field) + getattr(spend, field))
        if breach := task.spent.exceeds(task.budget):
            raise BudgetExceeded(
                f"task exceeded its {breach} budget",
                task_id=str(task_id),
                limit=getattr(task.budget, breach),
                spent=getattr(task.spent, breach),
            )

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
