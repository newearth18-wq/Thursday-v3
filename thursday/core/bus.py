"""Event bus (§79).

In-process fan-out for development and tests; the Redis Streams implementation swaps in
behind the same port for production. Handlers are isolated: one failing subscriber never
takes down the publisher, because an audit writer must not be able to abort a task.
"""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Awaitable, Callable

from thursday.core.logging import get_logger
from thursday.shared.models import Event

log = get_logger(__name__)

Handler = Callable[[Event], Awaitable[None]]


class InProcessEventBus:
    def __init__(self, *, history_limit: int = 500) -> None:
        self._handlers: list[tuple[str, Handler]] = []
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._seen: set[str] = set()

    def subscribe(self, pattern: str, handler: Handler) -> None:
        """``pattern`` is a glob over the event kind, e.g. ``task.*`` or ``*``."""
        self._handlers.append((pattern, handler))

    async def publish(self, event: Event) -> None:
        # At-least-once delivery upstream means handlers must be idempotent; we help by
        # dropping exact replays of an event id.
        key = str(event.id)
        if key in self._seen:
            return
        self._seen.add(key)

        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        matched = [h for pattern, h in self._handlers if fnmatch.fnmatch(event.kind, pattern)]
        if not matched:
            return
        results = await asyncio.gather(
            *(handler(event) for handler in matched), return_exceptions=True
        )
        for handler, result in zip(matched, results, strict=True):
            if isinstance(result, BaseException):
                log.error(
                    "event_handler_failed",
                    kind=event.kind,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    error=str(result),
                )

    def history(self, pattern: str = "*", limit: int = 100) -> list[Event]:
        return [e for e in self._history if fnmatch.fnmatch(e.kind, pattern)][-limit:]
