"""Event bus (§79).

In-process fan-out for development and tests; the Redis Streams implementation swaps in
behind the same port for production. Handlers are isolated: one failing subscriber never
takes down the publisher, because an audit writer must not be able to abort a task.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from thursday_shared.models import Event

from thursday_core.logging import get_logger

log = get_logger(__name__)

Handler = Callable[[Event], Awaitable[None]]
#: A subscriber that does its work inline and returns nothing.
SyncHandler = Callable[[Event], Any]


#: How many recently-seen event ids the replay guard remembers. Deliberately much larger
#: than the history window: history is for reading back, and this is for *not* re-delivering
#: something, which has to keep working across a burst that has long left the history.
DEDUPE_LIMIT = 8192


class InProcessEventBus:
    def __init__(self, *, history_limit: int = 500, dedupe_limit: int = DEDUPE_LIMIT) -> None:
        self._handlers: list[tuple[str, Handler | SyncHandler]] = []
        self._history: list[Event] = []
        self._history_limit = history_limit
        # A dict, not a set, because insertion order is what makes eviction possible — and
        # eviction is the whole point. This was an unbounded `set` until the audit in
        # Sprint 86: every event id Thursday had ever published, kept forever, on the
        # hottest path in the system. `_history` two lines up was bounded from the start,
        # which is what makes the omission a slip rather than a decision.
        self._seen: dict[str, None] = {}
        self._dedupe_limit = dedupe_limit

    def subscribe(self, pattern: str, handler: Handler | SyncHandler) -> None:
        """``pattern`` is a glob over the event kind, e.g. ``task.*`` or ``*``."""
        self._handlers.append((pattern, handler))

    async def publish(self, event: Event) -> None:
        # At-least-once delivery upstream means handlers must be idempotent; we help by
        # dropping exact replays of an event id.
        key = str(event.id)
        if key in self._seen:
            return
        self._seen[key] = None
        while len(self._seen) > self._dedupe_limit:
            # Oldest first. A replay older than the window is delivered again, which is
            # exactly the at-least-once contract this class documents; handlers are
            # required to be idempotent and the guard is a courtesy, not a guarantee.
            self._seen.pop(next(iter(self._seen)))

        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        matched = [h for pattern, h in self._handlers if fnmatch.fnmatch(event.kind, pattern)]
        if not matched:
            return
        results = await asyncio.gather(
            *(self._deliver(handler, event) for handler in matched), return_exceptions=True
        )
        for handler, result in zip(matched, results, strict=True):
            if isinstance(result, BaseException):
                log.error(
                    "event_handler_failed",
                    kind=event.kind,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    error=str(result),
                )

    async def _deliver(self, handler: Handler | SyncHandler, event: Event) -> None:
        """Call one subscriber, async or not.

        A synchronous subscriber is an easy thing to write — appending to a list, bumping a
        counter — and until this wrapper existed it did not merely fail, it raised out of
        ``publish`` before *any* handler ran, aborting the task that published the event.
        That is precisely what this class promises cannot happen, so it is handled here
        rather than left to every subscriber to remember.
        """
        outcome = handler(event)
        if inspect.isawaitable(outcome):
            await outcome

    def history(self, pattern: str = "*", limit: int = 100) -> list[Event]:
        return [e for e in self._history if fnmatch.fnmatch(e.kind, pattern)][-limit:]
