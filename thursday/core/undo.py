"""Undo / rollback (§40).

Every reversible action registers how to reverse it *before* the result is reported. The
Permission Engine reads the same registry: an action with no undo path is treated as
irreversible and therefore needs approval (§8.2 rule 5).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday.core.logging import get_logger
from thursday.shared.errors import ThursdayError
from thursday.shared.models import UndoRecord

log = get_logger(__name__)

#: action → the action that reverses it. ``None`` means "no automatic reversal exists".
INVERSE_ACTIONS: dict[str, str | None] = {
    "open_app": "close_app",
    "close_app": "open_app",
    "create_folder": "delete_folder",
    "write_file": "restore_file",
    "save_file": "restore_file",
    "move": "move",
    "rename": "rename",
    "copy": "delete",
    "delete": "restore_from_trash",
    "clipboard_set": "clipboard_set",
    "set_volume": "set_volume",
    "obsidian_write": "obsidian_restore",
    "memory_write": "memory_forget",
    # No inverse exists for these; policy treats them as irreversible.
    "send_email": None,
    "send_message": None,
    "publish": None,
    "purchase": None,
    "run_shell": None,
    "install_software": None,
    "power": None,
}

UndoExecutor = Callable[[UndoRecord], Awaitable[bool]]


def is_reversible(action: str) -> bool:
    return INVERSE_ACTIONS.get(action, None) is not None


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
