"""Background jobs (§43, §59).

Long work never blocks the conversation. In development these run as asyncio tasks in the
same process; in production they run behind the Redis queue with the same signatures, so
moving them out is a deployment change rather than a rewrite.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.enums import NotificationPriority, TaskState
from thursday_shared.models import Event

from thursday_worker.queue import job

log = get_logger(__name__)

#: The container the registered jobs run against. Set once at worker start-up — the job
#: functions stay plain callables so they can be invoked directly in a test.
_container: Any = None


def bind_container(container: Any) -> None:
    global _container
    _container = container


def _require_container() -> Any:
    if _container is None:
        raise RuntimeError("no container bound; call bind_container() at worker start-up")
    return _container


@job("execute_task")
async def execute_task(*, task_id: str) -> dict[str, Any]:
    """PART 43. Run a queued task off the conversation path.

    Registered as non-retryable in DramatiqQueue: re-running a task that already moved a
    file is not a retry, it is a second move.
    """
    from uuid import UUID

    c = _require_container()
    task = c.tasks.get(UUID(task_id))
    if task is None:
        return {"error": "unknown task"}
    if task.status.is_terminal:
        return {"status": task.status.value, "note": "already finished"}
    if task.plan is None:
        return {"error": "task has no plan"}

    context = getattr(task, "_context", None)
    if context is None:
        return {"error": "task has no execution context"}
    outcome = await c.orchestrator.run(task, task.plan, context)
    return {"ok": outcome.ok, "summary": outcome.summary()}


@job("run_automation")
async def run_automation(*, automation_id: str) -> dict[str, Any]:
    from uuid import UUID

    c = _require_container()
    target = next((a for a in c.automations.list() if a.id == UUID(automation_id)), None)
    if target is None:
        return {"error": "unknown automation"}
    return {"results": await c.automations.run(target)}


@job("decay_memory")
async def decay_memory() -> dict[str, Any]:
    return {"removed": await _require_container().memory.decay()}


@job("index_document")
async def index_document(*, path: str) -> dict[str, Any]:
    """Placeholder for the Phase-3 document indexer. Registered now so the queue's shape
    is exercised, and honest about doing nothing yet."""
    log.info("document_indexing_not_implemented", path=path)
    return {"indexed": False, "reason": "the document indexer lands in Phase 3"}


@dataclass
class JobSchedule:
    memory_decay_s: float = 3600.0
    health_check_s: float = 60.0
    device_liveness_s: float = 30.0
    approval_sweep_s: float = 30.0


class BackgroundWorker:
    """Runs the periodic jobs Thursday needs in order to stay honest about its own state."""

    def __init__(self, container: object, schedule: JobSchedule | None = None) -> None:
        self.c = container
        self.schedule = schedule or JobSchedule()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(
                self._loop(self.schedule.memory_decay_s, self.decay_memory), name="memory-decay"
            ),
            asyncio.create_task(
                self._loop(self.schedule.health_check_s, self.check_health), name="health"
            ),
            asyncio.create_task(
                self._loop(self.schedule.device_liveness_s, self.check_devices), name="devices"
            ),
            asyncio.create_task(
                self._loop(self.schedule.approval_sweep_s, self.sweep_approvals), name="approvals"
            ),
        ]
        log.info("worker_started", jobs=[t.get_name() for t in self._tasks])

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _loop(self, interval: float, job) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning(
                    "background_job_failed", job=getattr(job, "__name__", "?"), error=str(exc)
                )

    # ------------------------------------------------------------------ jobs

    async def decay_memory(self) -> int:
        """Drop expired working memory. Long-term layers decay in score, not existence."""
        removed = await self.c.memory.decay()  # type: ignore[attr-defined]
        if removed:
            log.debug("memory_decayed", removed=removed)
        return removed

    async def check_health(self) -> None:
        checks = await self.c.health()  # type: ignore[attr-defined]
        failing = [c for c in checks if not c["ok"]]
        if failing:
            await self.c.bus.publish(  # type: ignore[attr-defined]
                Event(
                    kind="system.health",
                    priority=NotificationPriority.IMPORTANT,
                    payload={"failing": [c["component"] for c in failing]},
                )
            )

    async def check_devices(self) -> None:
        """A node that stopped heart-beating is offline, whether or not the socket noticed."""
        cutoff = datetime.now(UTC) - timedelta(seconds=90)
        for summary in self.c.hub.online():  # type: ignore[attr-defined]
            if summary.last_seen_at and summary.last_seen_at < cutoff:
                log.warning(
                    "device_stale", device=summary.name, last_seen=str(summary.last_seen_at)
                )
                await self.c.hub.unregister(summary.id)  # type: ignore[attr-defined]

    async def sweep_approvals(self) -> None:
        """Expire pending approvals. Silence is never consent (§38)."""
        expired = [
            a
            for a in list(self.c.approvals._pending.values())
            if a.expires_at and a.expires_at <= datetime.now(UTC) and a.state.value == "pending"
        ]
        for approval in expired:
            await self.c.approvals.decide(approval.id, approve=False, note="expired")  # type: ignore[attr-defined]

    async def consolidate(self) -> int:
        """Turn completed tasks into episodic memory the conversation loop did not capture."""
        written = 0
        for task in self.c.tasks.list(status=TaskState.COMPLETED, limit=50):  # type: ignore[attr-defined]
            if task.result.get("consolidated"):
                continue
            task.result["consolidated"] = True
            written += 1
        return written
