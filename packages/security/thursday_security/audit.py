"""Audit log (§39) — append-only, hash-chained (threat T10), and durable.

Every action Thursday takes is recorded with enough context to answer "who did what, on
whose behalf, with whose permission, and what happened". Payloads are redacted projections,
never raw arguments.

One property shapes how a failed write is handled here, and it is easy to miss:

    `verify_chain` detects an entry that was altered or removed. It cannot detect an
    entry that was never written — a missing entry leaves a perfectly valid chain.

So a dropped audit write is invisible to the exact mechanism that exists to catch tampering.
That rules out the usual "log the error and carry on": the error would be the only trace, in
the log nobody keeps. `record` therefore raises when it cannot persist, and marks itself
`degraded` so the failure is visible in health and in the morning brief rather than only in
whatever caught the exception.

What the caller does with that raise is the caller's judgement, and it differs by moment.
Before an action has run, failing is free and correct. After it has run, failing invites a
retry — and §194 forbids silently duplicating an external communication. `execution.py`
makes that distinction explicitly at each call site.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from thursday_core.logging import get_logger
from thursday_shared.ids import current_trace_id, new_id

from thursday_security.redaction import SecretRedactor, redact_dict

log = get_logger(__name__)

GENESIS = "0" * 64


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=new_id)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: Literal["user", "thursday", "agent", "automation", "system"] = "thursday"
    agent: str | None = None
    task_id: UUID | None = None
    device_id: UUID | None = None
    #: The machine the instruction came *from*, when that is not the machine it ran on.
    #: "Who told my PC to do that, and from where" is not answerable after the fact from
    #: an entry that records only the target (§9.4, V8).
    origin_device_id: UUID | None = None
    tool: str | None = None
    action: str = ""
    resource: str = ""
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    result: Literal["ok", "failed", "blocked", "pending", "unverified"] = "ok"
    permission_decision: str | None = None
    approval_id: UUID | None = None
    error: str | None = None
    trace_id: str = Field(default_factory=current_trace_id)
    prev_hash: str = GENESIS
    hash: str = ""

    def compute_hash(self) -> str:
        body = self.model_dump(mode="json", exclude={"hash"})
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


class AuditWriteError(Exception):
    """An entry that reached the chain and not the storage behind it."""


class AuditLog:
    """The chain, in memory and on disk.

    The production writer inserts into ``audit_logs``, on a role with no UPDATE or DELETE
    grant; the chain is what makes deletion detectable rather than merely forbidden.
    """

    def __init__(self, redactor: SecretRedactor | None = None, *, repository: Any = None) -> None:
        self._entries: list[AuditEntry] = []
        self._redactor = redactor or SecretRedactor()
        from thursday_core.persistence import NullRepository

        self._repository = repository or NullRepository()
        #: Set when an entry could not be persisted. Never cleared by a later success: the
        #: gap it marks does not heal, and a flag that goes green again would say the log is
        #: complete when it is not.
        self.degraded = False
        self.lost = 0

    async def record(self, entry: AuditEntry) -> AuditEntry:
        """Chain an entry and store it. Raises if it could not be stored.

        The in-memory append happens first and cannot fail, so the entry is queryable now
        whatever the storage does. That ordering is deliberate: an entry missing from the
        chain would be undetectable, while one missing from the table is at least visible
        while this process lives, and `degraded` says so out loud afterwards.
        """
        entry.input_summary = redact_dict(entry.input_summary, self._redactor)
        entry.output_summary = redact_dict(entry.output_summary, self._redactor)
        if entry.error:
            entry.error = self._redactor.redact(entry.error).text
        entry.prev_hash = self._entries[-1].hash if self._entries else GENESIS
        entry.hash = entry.compute_hash()
        self._entries.append(entry)

        try:
            await self._repository.put(entry.model_dump(mode="python"))
        except Exception as exc:
            self.degraded = True
            self.lost += 1
            log.error("audit_write_failed", action=entry.action, error=str(exc))
            raise AuditWriteError(
                f"the audit entry for {entry.action!r} is in this session's chain and could "
                "not be stored; the record of what Thursday did is now incomplete"
            ) from exc
        return entry

    async def restore(self) -> int:
        """Load the chain, in the order it was written.

        Order is the whole point. `verify_chain` walks entries comparing each `prev_hash` to
        the one before it, so rows returned in whatever order the engine felt like would fail
        a chain that is perfectly intact — and, worse, a *reordered* chain that happened to
        verify would be a chain nobody could trust.
        """
        rows = await self._repository.load()
        restored = 0
        for row in rows:
            try:
                self._entries.append(AuditEntry.model_validate(row))
                restored += 1
            except ValidationError as exc:
                # A dropped row breaks the chain from that point, which `verify_chain`
                # reports. That is the correct outcome and not something to paper over.
                self.degraded = True
                self.lost += 1
                log.error("audit_row_unreadable", error=str(exc))
        if restored:
            log.info("audit_restored", entries=restored, chain_intact=self.verify_chain())
        return restored

    def health(self) -> dict:
        """What the health endpoint and the brief both read."""
        return {
            "entries": len(self._entries),
            "chain_intact": self.verify_chain(),
            "degraded": self.degraded,
            "lost": self.lost,
        }

    # ------------------------------------------------------------------ backup (Sprint 47)

    def export_state(self) -> list[dict]:
        return [entry.model_dump(mode="json") for entry in self._entries]

    def import_state(self, rows: list[dict], *, replace: bool = True) -> int:
        """Restore audit entries with their hashes untouched.

        Not re-recorded through `record`, which would recompute `prev_hash` and `hash` — and
        a chain recomputed on restore is a chain that verifies whatever it was given. The
        point of keeping the stored hashes is that `verify_chain` can still catch a backup
        somebody edited.
        """
        if replace:
            self._entries.clear()
        for row in rows:
            self._entries.append(AuditEntry.model_validate(row))
        return len(rows)

    def verify_chain(self) -> bool:
        """True if no entry was altered or removed since it was written."""
        previous = GENESIS
        for entry in self._entries:
            if entry.prev_hash != previous or entry.hash != entry.compute_hash():
                return False
            previous = entry.hash
        return True

    def entries(
        self,
        *,
        task_id: UUID | None = None,
        trace_id: str | None = None,
        device_id: UUID | None = None,
        actor: str | None = None,
        tool: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Filter the trail.

        ``trace_id`` is the one that matters when something has gone wrong: it is stamped on
        the request and carried through core, agent, tool and device, so it reassembles one
        conversation turn across every component that touched it. A task id only covers the
        part that became a task; the turns that were refused before that never had one.
        """
        rows = self._entries
        if task_id is not None:
            rows = [e for e in rows if e.task_id == task_id]
        if trace_id is not None:
            rows = [e for e in rows if e.trace_id == trace_id]
        if device_id is not None:
            rows = [e for e in rows if e.device_id == device_id]
        if actor is not None:
            rows = [e for e in rows if e.actor == actor]
        if tool is not None:
            rows = [e for e in rows if e.tool == tool]
        return rows[-limit:]

    def __len__(self) -> int:
        return len(self._entries)
