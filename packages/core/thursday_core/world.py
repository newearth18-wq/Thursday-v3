"""World State (§12).

What "this", "that file", "just now" and "continue" resolve to. Kept current by a projector
subscribed to the event bus, so no component has to remember to update it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from thursday_shared.models import DeviceSummary, Event, WorldStateSnapshot


class WorldState:
    def __init__(self) -> None:
        self._snapshot = WorldStateSnapshot()

    def snapshot(self) -> WorldStateSnapshot:
        return self._snapshot.model_copy(deep=True)

    def update(self, **fields: object) -> WorldStateSnapshot:
        for key, value in fields.items():
            if not hasattr(self._snapshot, key):
                raise AttributeError(f"world state has no field {key!r}")
            setattr(self._snapshot, key, value)
        self._snapshot.updated_at = datetime.now(UTC)
        return self.snapshot()

    def set_devices(self, devices: list[DeviceSummary]) -> None:
        self._snapshot.online_devices = list(devices)
        self._snapshot.updated_at = datetime.now(UTC)

    def note_action(
        self, *, action: str, resource: str = "", device_id: UUID | None = None
    ) -> None:
        """Feeds "ทำต่อ" / "undo that" / "the file just now"."""
        entry = {
            "action": action,
            "resource": resource,
            "device_id": str(device_id) if device_id else None,
            "at": datetime.now(UTC).isoformat(),
        }
        self._snapshot.recent_actions = [entry, *self._snapshot.recent_actions][:20]
        if resource and ("/" in resource or "\\" in resource or "." in resource):
            self._snapshot.last_referenced_file = resource
        self._snapshot.updated_at = datetime.now(UTC)

    def resolve_reference(self, phrase: str) -> str | None:
        """Best-effort deixis: 'that file', 'ไฟล์นั้น', 'อันเมื่อกี้'."""
        lowered = phrase.lower()
        if any(w in lowered for w in ("that file", "ไฟล์นั้น", "ไฟล์เมื่อกี้", "the file")):
            return self._snapshot.last_referenced_file
        if any(w in lowered for w in ("this device", "เครื่องนี้", "here")):
            return self._snapshot.active_device_name
        return None


class WorldStateProjector:
    """Subscribes to the bus and keeps the snapshot honest (§10.3)."""

    def __init__(self, world: WorldState) -> None:
        self.world = world

    def attach(self, bus: object) -> None:
        # `*` first, and deliberately not folded into the five handlers below. "When did
        # anything last happen" is one fact, and five call sites that each have to remember
        # to stamp it is five chances to forget — the next handler added would silently be
        # the one that lets Thursday fall asleep mid-task (Sprint 85).
        bus.subscribe("*", self.on_any)  # type: ignore[attr-defined]
        bus.subscribe("device.*", self.on_device)  # type: ignore[attr-defined]
        bus.subscribe("task.*", self.on_task)  # type: ignore[attr-defined]
        bus.subscribe("agent.*", self.on_agent)  # type: ignore[attr-defined]
        bus.subscribe("approval.*", self.on_approval)  # type: ignore[attr-defined]
        bus.subscribe("tool.executed", self.on_tool)  # type: ignore[attr-defined]

    async def on_any(self, event: Event) -> None:
        """Thursday was awake at this moment, whatever the event was.

        Nothing on the bus is periodic — there is no heartbeat kind, and the expression feed
        publishes nothing — so a genuinely idle Thursday genuinely goes quiet here, which is
        what makes `SLEEPING` a derived fact rather than a timer somebody set.
        """
        self.world.update(last_event_at=datetime.now(UTC))

    async def on_device(self, event: Event) -> None:
        if event.kind == "device.connected" and event.device_id:
            self.world.update(active_device_id=event.device_id)
            name = event.payload.get("name")
            if name:
                self.world.update(active_device_name=str(name))
        elif event.kind == "device.disconnected":
            snap = self.world.snapshot()
            if snap.active_device_id == event.device_id:
                self.world.update(active_device_id=None, active_device_name=None)

    async def on_task(self, event: Event) -> None:
        if event.kind in ("task.created", "task.started") and event.task_id:
            self.world.update(active_task_id=event.task_id)
        elif event.kind in ("task.completed", "task.failed", "task.cancelled"):
            snap = self.world.snapshot()
            if snap.active_task_id == event.task_id:
                self.world.update(active_task_id=None, last_referenced_task_id=event.task_id)

    async def on_agent(self, event: Event) -> None:
        """Keep `running_agents` to agents that are running, and remember how work ended.

        It used to keep finished agents in the dict with a "completed" or "failed" value,
        which made the field's name untrue and — once Sprint 80 derived a mood from it —
        would have left Thursday visibly sorry about a failure from an hour ago, forever.
        So an agent leaves the dict when it stops, and the *time* it stopped is recorded
        instead, which is what lets the feeling fade.
        """
        agent = str(event.payload.get("agent", ""))
        if not agent:
            return
        snapshot = self.world.snapshot()
        running = dict(snapshot.running_agents)
        fields: dict[str, object] = {}

        if event.kind == "agent.started":
            running[agent] = "working"
            # The allowlisted phrase, never the name. `plain.activity` already produced it
            # at the emitting end (Sprint 65); reading anything else here would be the leak.
            fields["current_activity"] = str(event.payload.get("activity", ""))
        else:
            running.pop(agent, None)
            if event.kind == "agent.completed":
                fields["last_success_at"] = datetime.now(UTC)
            elif event.kind == "agent.failed":
                fields["last_failure_at"] = datetime.now(UTC)
            if not running:
                # Nothing is running, so there is nothing being done. A leftover phrase
                # under a finished job reads as work still in progress.
                fields["current_activity"] = ""

        self.world.update(running_agents=running, **fields)

    async def on_approval(self, event: Event) -> None:
        snap = self.world.snapshot()
        pending = list(snap.pending_approvals)
        approval_id = event.payload.get("id")
        if not approval_id:
            return
        parsed = UUID(str(approval_id))
        if event.kind == "approval.required":
            if parsed not in pending:
                pending.append(parsed)
        else:
            pending = [a for a in pending if a != parsed]
        self.world.update(pending_approvals=pending)

    async def on_tool(self, event: Event) -> None:
        self.world.note_action(
            action=str(event.payload.get("tool", "")),
            resource=str(event.payload.get("resource", "")),
            device_id=event.device_id,
        )
