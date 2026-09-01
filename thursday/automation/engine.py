"""Automation engine (§47, §48) and the proactivity gate (§46).

Rules are matched against the event stream. What a rule may *do* is still decided by the
Permission Engine when it runs — automation changes when Thursday acts, never what it is
allowed to do.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday.automation.rules import Automation
from thursday.core.logging import get_logger
from thursday.shared.enums import NotificationPriority, ProactivityLevel
from thursday.shared.models import Event, ToolCall

log = get_logger(__name__)

#: Ceiling on unsolicited notifications per hour, per priority (§46 "no nagging").
RATE_LIMIT_PER_HOUR: dict[NotificationPriority, int] = {
    NotificationPriority.CRITICAL: 20,
    NotificationPriority.IMPORTANT: 6,
    NotificationPriority.NORMAL: 3,
    NotificationPriority.LOW: 1,
}

#: The minimum proactivity level at which each priority may interrupt the owner.
_MIN_LEVEL: dict[NotificationPriority, ProactivityLevel] = {
    NotificationPriority.CRITICAL: ProactivityLevel.LOW,
    NotificationPriority.IMPORTANT: ProactivityLevel.NORMAL,
    NotificationPriority.NORMAL: ProactivityLevel.NORMAL,
    NotificationPriority.LOW: ProactivityLevel.HIGH,
}


class ProactivityGate:
    """Decides whether Thursday may speak up unprompted."""

    def __init__(self, level: ProactivityLevel = ProactivityLevel.NORMAL) -> None:
        self.level = level
        self._recent: list[tuple[datetime, NotificationPriority]] = []

    def allows(
        self,
        priority: NotificationPriority,
        *,
        owner_status: str = "available",
        people_present: int = 1,
        private: bool = False,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        now = now or datetime.now(UTC)
        if self.level is ProactivityLevel.OFF:
            return False, "proactivity is off"
        if self.level < _MIN_LEVEL[priority]:
            return False, f"{priority} is below the current proactivity level"
        if owner_status in ("dnd", "asleep") and priority is not NotificationPriority.CRITICAL:
            return False, f"owner status is {owner_status}"
        # §67: private content is not announced while someone else may hear it.
        if private and people_present > 1:
            return False, "another person is present"

        cutoff = now - timedelta(hours=1)
        self._recent = [(t, p) for t, p in self._recent if t > cutoff]
        used = sum(1 for _, p in self._recent if p is priority)
        if used >= RATE_LIMIT_PER_HOUR[priority]:
            return False, f"hourly limit for {priority} reached"
        return True, "allowed"

    def record(self, priority: NotificationPriority, *, now: datetime | None = None) -> None:
        self._recent.append((now or datetime.now(UTC), priority))


class AutomationEngine:
    def __init__(
        self,
        *,
        bus: object,
        executor: object | None = None,
        tasks: object | None = None,
        world: object | None = None,
        gate: ProactivityGate | None = None,
    ) -> None:
        self._bus = bus
        self._executor = executor
        self._tasks = tasks
        self._world = world
        self.gate = gate or ProactivityGate()
        self._automations: dict[UUID, Automation] = {}

    def attach(self) -> None:
        self._bus.subscribe("*", self.on_event)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ registry

    def add(self, automation: Automation) -> Automation:
        if automation.created_by == "thursday_suggested" and automation.enabled:
            # §49: Thursday proposes; the owner enables. Never the other way round.
            automation.enabled = False
            log.info("suggested_automation_left_disabled", name=automation.name)
        self._automations[automation.id] = automation
        return automation

    def enable(self, automation_id: UUID, *, enabled: bool = True) -> Automation:
        automation = self._automations[automation_id]
        automation.enabled = enabled
        return automation

    def remove(self, automation_id: UUID) -> None:
        self._automations.pop(automation_id, None)

    def list(self, *, enabled_only: bool = False) -> list[Automation]:
        return [a for a in self._automations.values() if a.enabled or not enabled_only]

    # ------------------------------------------------------------------ execution

    async def on_event(self, event: Event) -> None:
        world = self._world.snapshot().model_dump(mode="json") if self._world else {}
        for automation in list(self._automations.values()):
            if automation.should_fire(event, world=world, proactivity=self.gate.level):
                await self.run(automation, event)

    async def run(self, automation: Automation, event: Event | None = None) -> list[Any]:
        # NB: structlog reserves the keyword "event" for the message itself.
        log.info(
            "automation_triggered",
            name=automation.name,
            trigger_event=event.kind if event else "manual",
        )
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(kind="automation.triggered", payload={"automation": automation.name})
        )
        outputs: list[Any] = []
        for action in [*automation.actions, *automation.follow_ups]:
            outputs.append(await self._perform(automation, action, event))
        automation.mark_run()
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                kind="automation.completed",
                payload={"automation": automation.name, "steps": len(outputs)},
            )
        )
        return outputs

    async def _perform(self, automation: Automation, action: Any, event: Event | None) -> Any:
        if action.kind == "notify":
            allowed, reason = self.gate.allows(
                automation.priority,
                owner_status=str(
                    self._world.snapshot().owner_status if self._world else "available"
                ),
                people_present=(self._world.snapshot().people_present if self._world else 1),
                private=bool(action.args.get("private")),
            )
            if not allowed:
                log.debug("notification_suppressed", automation=automation.name, reason=reason)
                return {"notified": False, "reason": reason}
            self.gate.record(automation.priority)
            await self._bus.publish(  # type: ignore[attr-defined]
                Event(
                    kind="notification.raised",
                    priority=automation.priority,
                    payload={"title": action.args.get("title", automation.name), **action.args},
                )
            )
            return {"notified": True}

        if action.kind == "task" and self._tasks is not None:
            task = await self._tasks.create(  # type: ignore[attr-defined]
                title=action.name or automation.name,
                objective=str(action.args.get("objective", action.name)),
                budget=automation.budget,
            )
            return {"task_id": str(task.id)}

        if action.kind in ("tool", "obsidian_write") and self._executor is not None:
            tool = "obsidian_write" if action.kind == "obsidian_write" else action.name
            # Still permission-checked: an automation is not an approval bypass.
            result = await self._executor.execute(  # type: ignore[attr-defined]
                ToolCall(tool=tool, args=action.args, reason=f"automation: {automation.name}"),
                agent=f"automation:{automation.name}",
                wait_for_approval=False,
            )
            return {"tool": tool, "ok": result.ok, "verified": result.verified}

        return {"skipped": action.kind}
