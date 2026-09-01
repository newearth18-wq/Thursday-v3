"""Audit log (§39) — append-only and hash-chained (threat T10).

Every action Thursday takes is recorded with enough context to answer "who did what, on
whose behalf, with whose permission, and what happened". Payloads are redacted projections,
never raw arguments.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from thursday_shared.ids import current_trace_id, new_id

from thursday_security.redaction import SecretRedactor, redact_dict

GENESIS = "0" * 64


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=new_id)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: Literal["user", "thursday", "agent", "automation", "system"] = "thursday"
    agent: str | None = None
    task_id: UUID | None = None
    device_id: UUID | None = None
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


class AuditLog:
    """In-memory chain with the same semantics as the Postgres append-only table.

    The production writer inserts into ``audit_logs``, on a role with no UPDATE or DELETE
    grant; the chain is what makes deletion detectable rather than merely forbidden.
    """

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        self._entries: list[AuditEntry] = []
        self._redactor = redactor or SecretRedactor()

    def record(self, entry: AuditEntry) -> AuditEntry:
        entry.input_summary = redact_dict(entry.input_summary, self._redactor)
        entry.output_summary = redact_dict(entry.output_summary, self._redactor)
        if entry.error:
            entry.error = self._redactor.redact(entry.error).text
        entry.prev_hash = self._entries[-1].hash if self._entries else GENESIS
        entry.hash = entry.compute_hash()
        self._entries.append(entry)
        return entry

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
        actor: str | None = None,
        tool: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        rows = self._entries
        if task_id is not None:
            rows = [e for e in rows if e.task_id == task_id]
        if actor is not None:
            rows = [e for e in rows if e.actor == actor]
        if tool is not None:
            rows = [e for e in rows if e.tool == tool]
        return rows[-limit:]

    def __len__(self) -> int:
        return len(self._entries)
