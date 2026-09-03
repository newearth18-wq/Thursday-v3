"""Undo / rollback (§40).

Every reversible action registers how to reverse it *before* the result is reported. The
Permission Engine reads the same registry: an action with no undo path is treated as
irreversible and therefore needs approval (§8.2 rule 5).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday_shared.actions import canonical
from thursday_shared.errors import ThursdayError
from thursday_shared.models import UndoRecord

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: action → the action that reverses it. ``None`` means "no automatic reversal exists".
INVERSE_ACTIONS: dict[str, str | None] = {
    "app.open": "app.close",
    "app.close": "app.open",
    "file.folder.create": "file.folder.delete",
    "file.write": "file.restore",
    "file.create": "file.delete",
    "file.move": "file.move",
    "file.rename": "file.rename",
    "file.copy": "file.delete",
    "file.delete": "file.restore_from_trash",
    "clipboard.write": "clipboard.write",
    "audio.volume.set": "audio.volume.set",
    "obsidian.write": "obsidian.restore",
    "memory.write": "memory.forget",
    # No inverse exists for these; policy treats them as irreversible.
    "email.send": None,
    "message.send": None,
    "social.post": None,
    "purchase.make": None,
    "shell.run": None,
    "powershell.run": None,
    "app.install": None,
    "system.power": None,
}

UndoExecutor = Callable[[UndoRecord], Awaitable[bool]]


#: Actions that observe without changing anything. They need no inverse, so treating them
#: as irreversible would wrongly mark every read-only step as dangerous.
NON_MUTATING_ACTIONS: frozenset[str] = frozenset(
    {
        "file.read",
        "file.list",
        "file.search",
        "file.open",
        "window.active",
        "screen.capture",
        "system.info",
        "system.process.list",
        "audio.volume.get",
        "clipboard.read",
        "memory.search",
        "obsidian.search",
        "web.search",
        "clock.now",
        "browser.open",
        "browser.read",
        "vision.analyze",
        "camera.capture",
    }
)


def is_reversible(action: str) -> bool:
    """True when the action can be undone — or has nothing to undo."""
    name = canonical(action)
    return name in NON_MUTATING_ACTIONS or INVERSE_ACTIONS.get(name) is not None


def is_destructive(action: str) -> bool:
    """True when the action changes something and no reversal exists."""
    name = canonical(action)
    return name not in NON_MUTATING_ACTIONS and INVERSE_ACTIONS.get(name) is None


class UndoRegistry:
    def __init__(self, *, ttl_hours: int = 48) -> None:
        self._records: dict[UUID, UndoRecord] = {}
        self._order: list[UUID] = []
        self._executors: dict[str, UndoExecutor] = {}
        self._ttl = timedelta(hours=ttl_hours)

    def register_executor(self, operation: str, executor: UndoExecutor) -> None:
        self._executors[operation] = executor

    def record(self, undo: UndoRecord) -> UndoRecord:
        undo.expires_at = undo.expires_at or datetime.now(UTC) + self._ttl
        self._records[undo.action_id] = undo
        self._order.append(undo.action_id)
        return undo

    def get(self, action_id: UUID) -> UndoRecord | None:
        record = self._records.get(action_id)
        if record and record.expires_at and record.expires_at <= datetime.now(UTC):
            return None
        return record

    def last(self) -> UndoRecord | None:
        """Backs "Thursday, undo that"."""
        for action_id in reversed(self._order):
            if (record := self.get(action_id)) is not None:
                return record
        return None

    async def undo(self, action_id: UUID) -> bool:
        record = self.get(action_id)
        if record is None:
            raise ThursdayError("nothing to undo for that action", action_id=str(action_id))
        executor = self._executors.get(record.operation)
        if executor is None:
            raise ThursdayError(
                f"no executor registered for undo operation {record.operation!r}",
                action_id=str(action_id),
            )
        ok = await executor(record)
        if ok:
            self._records.pop(action_id, None)
            log.info("undo_executed", operation=record.operation, action_id=str(action_id))
        return ok

    def pending(self, limit: int = 20) -> list[UndoRecord]:
        out = [r for aid in reversed(self._order) if (r := self.get(aid)) is not None]
        return out[:limit]
