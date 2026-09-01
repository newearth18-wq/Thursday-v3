"""Automation rules (§48).

    WHEN <trigger>  IF <conditions>  DO <actions>  THEN <follow-ups>

Two properties matter more than expressiveness:

* An automation is not an approval bypass. Its actions still pass the Permission Engine at
  run time, so a rule cannot quietly acquire authority its author lacked.
* Every automation has a budget and a proactivity floor, so a misfiring rule costs a
  bounded amount of money and a bounded amount of the owner's attention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from thursday_shared.enums import NotificationPriority, ProactivityLevel
from thursday_shared.ids import new_id
from thursday_shared.models import Budget, Event

TriggerKind = Literal["schedule", "event", "state_change", "manual"]


@dataclass
class Trigger:
    kind: TriggerKind = "event"
    #: For ``event``: a glob over the event kind, e.g. ``file.created``.
    event_kind: str = "*"
    #: For ``schedule``: five-field cron, evaluated in the owner's timezone.
    cron: str | None = None
    #: For ``state_change``: a world-state field name.
    field: str | None = None

    def matches_event(self, event: Event) -> bool:
        from fnmatch import fnmatch

        return self.kind == "event" and fnmatch(event.kind, self.event_kind)


@dataclass
class Condition:
    """A single ``field op value`` test against the event payload or world state."""

    field: str
    op: Literal["eq", "ne", "gt", "lt", "contains", "matches", "in"] = "eq"
    value: Any = None

    def evaluate(self, scope: dict[str, Any]) -> bool:
        actual = _lookup(scope, self.field)
        if self.op == "eq":
            return actual == self.value
        if self.op == "ne":
            return actual != self.value
        if self.op == "gt":
            return _numeric(actual) > _numeric(self.value)
        if self.op == "lt":
            return _numeric(actual) < _numeric(self.value)
        if self.op == "contains":
            return self.value in (actual or "")
        if self.op == "matches":
            return bool(re.search(str(self.value), str(actual or "")))
        if self.op == "in":
            return actual in (self.value or [])
        return False


@dataclass
class Action:
    """One step of DO/THEN. ``kind`` names a tool, a task, or a notification."""

    kind: Literal["tool", "task", "notify", "obsidian_write"]
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    device_hint: str | None = None


@dataclass
class Automation:
    id: UUID = field(default_factory=new_id)
    name: str = ""
    trigger: Trigger = field(default_factory=Trigger)
    conditions: list[Condition] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    follow_ups: list[Action] = field(default_factory=list)
    enabled: bool = False
    proactivity_min: ProactivityLevel = ProactivityLevel.NORMAL
    budget: Budget = field(default_factory=lambda: Budget(usd=0.20, seconds=120, tool_calls=10))
    #: "thursday_suggested" rules stay disabled until the owner accepts them (§49).
    created_by: Literal["user", "thursday_suggested"] = "user"
    priority: NotificationPriority = NotificationPriority.NORMAL
    last_run_at: datetime | None = None
    run_count: int = 0

    def should_fire(
        self, event: Event, *, world: dict[str, Any], proactivity: ProactivityLevel
    ) -> bool:
        if not self.enabled:
            return False
        if proactivity < self.proactivity_min:
            return False
        if not self.trigger.matches_event(event):
            return False
        scope = {"event": event.payload, "kind": event.kind, "world": world}
        return all(condition.evaluate(scope) for condition in self.conditions)

    def mark_run(self) -> None:
        self.last_run_at = datetime.now(UTC)
        self.run_count += 1


def _lookup(scope: dict[str, Any], path: str) -> Any:
    """Dotted lookup: ``event.status``, ``world.owner_status``."""
    current: Any = scope
    for part in path.split("."):
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
        if current is None:
            return None
    return current


def _numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
