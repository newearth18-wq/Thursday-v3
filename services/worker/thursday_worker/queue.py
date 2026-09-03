"""The task queue (PART 2, PART 43, ADR 0003).

Long work never blocks the conversation. Two implementations of one port:

* ``InProcessQueue`` — asyncio tasks, used by tests and a single-process install.
* ``DramatiqQueue`` — Redis-backed actors, used when the worker is a separate process.

The actors are thin wrappers over plain functions, so the same code path runs either way.
That is deliberate: a background job that only exists inside a broker is a background job
nobody can debug.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.ids import new_id

log = get_logger(__name__)

#: Registered job functions, by name. Both queues dispatch through this, so a job is
#: callable directly in a test without a broker or an event loop of its own.
JOBS: dict[str, Callable[..., Awaitable[Any]]] = {}


def job(name: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register a background job under a stable name."""

    def register(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        JOBS[name] = fn
        return fn

    return register


class InProcessQueue:
    """asyncio-backed. Correct, observable, and enough for one process."""

    name = "in-process"
    distributed = False

    def __init__(self, *, concurrency: int = 4) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._results: dict[str, Any] = {}

    async def enqueue(self, actor: str, **kwargs: Any) -> str:
        fn = JOBS.get(actor)
        if fn is None:
            raise KeyError(f"no job registered under {actor!r}")
        job_id = str(new_id())

        async def run() -> Any:
            async with self._semaphore:
                try:
                    result = await fn(**kwargs)
                    self._results[job_id] = result
                    return result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("job_failed", actor=actor, job_id=job_id, error=str(exc))
                    self._results[job_id] = {"error": str(exc)}
                    raise

        handle = asyncio.create_task(run(), name=f"job:{actor}:{job_id}")
        self._running[job_id] = handle
        handle.add_done_callback(lambda _: self._running.pop(job_id, None))
        return job_id

    async def cancel(self, job_id: str) -> bool:
        handle = self._running.get(job_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    def running(self) -> list[str]:
        return list(self._running)

    def result(self, job_id: str) -> Any:
        return self._results.get(job_id)

    async def drain(self) -> None:
        if self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)

    async def health(self) -> tuple[bool, str]:
        return True, f"in-process, {len(self._running)} running"


class DramatiqQueue:
    """Dramatiq over Redis. Chosen over Celery in ADR 0003 for having far fewer parts.

    Retries are capped and destructive jobs are never retried automatically (PART 62), which
    is enforced here rather than left to each job to remember.
    """

    name = "dramatiq"
    distributed = True

    #: Jobs that change the world in a way a blind retry could double.
    NON_RETRYABLE = frozenset({"execute_task", "run_automation", "run_skill"})

    def __init__(self, redis_url: str, *, max_retries: int = 2) -> None:
        self.redis_url = redis_url
        self.max_retries = max_retries
        self._broker: Any = None
        self._actors: dict[str, Any] = {}

    def _ensure_broker(self) -> Any:
        if self._broker is None:
            import dramatiq
            from dramatiq.brokers.redis import RedisBroker

            self._broker = RedisBroker(url=self.redis_url)
            dramatiq.set_broker(self._broker)
            for name, fn in JOBS.items():
                self._actors[name] = self._make_actor(name, fn)
        return self._broker

    def _make_actor(self, name: str, fn: Callable[..., Awaitable[Any]]) -> Any:
        import dramatiq

        retries = 0 if name in self.NON_RETRYABLE else self.max_retries

        @dramatiq.actor(actor_name=name, max_retries=retries, time_limit=600_000)
        def actor(**kwargs: Any) -> Any:
            # Dramatiq workers are synchronous; each message gets its own loop rather than
            # sharing one, so a job that leaves the loop dirty cannot poison the next.
            return asyncio.run(fn(**kwargs))

        return actor

    async def enqueue(self, actor: str, **kwargs: Any) -> str:
        if actor not in JOBS:
            raise KeyError(f"no job registered under {actor!r}")
        self._ensure_broker()
        message = self._actors[actor].send(**kwargs)
        return str(message.message_id)

    async def cancel(self, job_id: str) -> bool:
        # Dramatiq has no first-class cancel; the task's own CANCELLED state is what stops
        # the work, checked at each step by the orchestrator.
        log.info("queue_cancel_delegated_to_task_state", job_id=job_id)
        return False

    def running(self) -> list[str]:
        return []

    async def health(self) -> tuple[bool, str]:
        try:
            self._ensure_broker()
        except Exception as exc:
            return False, f"broker unavailable: {exc}"
        return True, f"dramatiq on {self.redis_url.rsplit('@', 1)[-1]}, {len(JOBS)} actors"


def build_queue(redis_url: str | None, *, concurrency: int = 4) -> Any:
    return DramatiqQueue(redis_url) if redis_url else InProcessQueue(concurrency=concurrency)
