"""World State (§12).

What "this", "that file", "just now" and "continue" resolve to. Kept current by a projector
subscribed to the event bus, so no component has to remember to update it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from thursday.shared.models import DeviceSummary, Event, WorldStateSnapshot


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

    def note_action(self, *, action: str, resource: str = "", device_id: UUID | None = None) -> None:
        """Feeds "ทำต่อ" / "undo that" / "the file just now"."""
        entry = {
            "action": action,
            "resource": resource,
            "device_id": str(device_id) if device_id else None,
            "at": datetime.now(UTC).isoformat(),
        }
        self._snapshot.recent_actions = ([entry] + self._snapshot.recent_actions)[:20]
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
        bus.subscribe("device.*", self.on_device)  # type: ignore[attr-defined]
        bus.subscribe("task.*", self.on_task)  # type: ignore[attr-defined]
        bus.subscribe("agent.*", self.on_agent)  # type: ignore[attr-defined]
        bus.subscribe("approval.*", self.on_approval)  # type: ignore[attr-defined]
        bus.subscribe("tool.executed", self.on_tool)  # type: ignore[attr-defined]

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
        agent = str(event.payload.get("agent", ""))
        if not agent:
            return
        running = dict(self.world.snapshot().running_agents)
        if event.kind == "agent.started":
            running[agent] = "working"
        elif event.kind == "agent.completed":
            running[agent] = "completed"
        elif event.kind == "agent.failed":
            running[agent] = "failed"
        self.world.update(running_agents=running)

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
