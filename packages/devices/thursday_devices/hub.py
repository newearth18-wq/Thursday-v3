"""Device hub — the core side of TNP/1 (§9).

Holds one session per connected node, keeps the registry of what each machine can do
(§57), and refuses actions a node never advertised *before* anything is dispatched.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import DeviceStatus
from thursday_shared.errors import DeviceActionFailed, DeviceUnavailable
from thursday_shared.ids import new_id
from thursday_shared.models import (
    DeviceAction,
    DeviceActionResult,
    DeviceCapabilities,
    DeviceSummary,
    DeviceTelemetry,
    Event,
)
from thursday_shared.protocol import (
    ActionFrame,
    ActionResultFrame,
    Hello,
    parse_frame,
)

from thursday_devices import actions as catalogue

log = get_logger(__name__)


class LoopbackDeviceSession:
    """A node running inside this process.

    Used by the desktop app (which embeds its own node) and by the integration tests, so
    the vertical slice is exercised through the same code path as a remote node minus the
    socket.
    """

    transport = "loopback"

    def __init__(self, *, device_id: UUID, name: str, executor: Any, kind: str = "desktop") -> None:
        self.device_id = device_id
        self.name = name
        self.kind = kind
        self.executor = executor
        self.capabilities: DeviceCapabilities = executor.adapter.capabilities()
        self.os = executor.adapter.os_name
        self.connected_at = datetime.now(UTC)
        self.last_seen_at = self.connected_at

    async def invoke(self, action: DeviceAction) -> DeviceActionResult:
        self.last_seen_at = datetime.now(UTC)
        return await self.executor.execute(action)

    async def ping(self) -> DeviceTelemetry:
        self.last_seen_at = datetime.now(UTC)
        return await self.executor.adapter.telemetry()

    async def close(self) -> None:  # pragma: no cover - nothing to tear down
        return None


class WebSocketDeviceSession:
    """A remote node. Actions are correlated by ``action_id`` so results can arrive late."""

    transport = "websocket"

    def __init__(self, websocket: Any, hello: Hello) -> None:
        self._ws = websocket
        self.device_id = hello.device_id
        self.name = hello.name
        self.kind = hello.kind
        self.os = hello.os
        self.capabilities = hello.capabilities
        self.telemetry = hello.telemetry
        self.connected_at = datetime.now(UTC)
        self.last_seen_at = self.connected_at
        self._pending: dict[UUID, asyncio.Future[DeviceActionResult]] = {}
        self._closed = False

    async def invoke(self, action: DeviceAction) -> DeviceActionResult:
        if self._closed:
            raise DeviceUnavailable("device session is closed", device=self.name)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[DeviceActionResult] = loop.create_future()
        self._pending[action.id] = future
        frame = ActionFrame(
            action_id=action.id,
            action=action.action,
            args=action.args,
            timeout_s=action.timeout_s,
            trace_id=action.trace_id,
        )
        await self._ws.send_text(frame.model_dump_json())
        try:
            return await asyncio.wait_for(future, timeout=action.timeout_s + 5)
        except TimeoutError as exc:
            raise DeviceActionFailed(
                f"{self.name} did not answer within {action.timeout_s:g}s",
                device=self.name,
                action=action.action,
            ) from exc
        finally:
            self._pending.pop(action.id, None)

    def deliver(self, frame: ActionResultFrame) -> None:
        """Called by the WS reader loop when a result arrives."""
        self.last_seen_at = datetime.now(UTC)
        future = self._pending.get(frame.action_id)
        if future is None or future.done():
            return
        future.set_result(
            DeviceActionResult(
                action_id=frame.action_id,
                ok=frame.ok,
                verified=frame.verified,
                evidence=frame.evidence,
                data=frame.data,
                error=frame.error,
                duration_ms=frame.duration_ms,
                undo=frame.undo,
            )
        )

    async def ping(self) -> DeviceTelemetry:
        return self.telemetry

    async def close(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DeviceUnavailable("device disconnected", device=self.name))
        self._pending.clear()


class DeviceHub:
    def __init__(self, bus: object | None = None) -> None:
        self._sessions: dict[UUID, Any] = {}
        self._known: dict[UUID, DeviceSummary] = {}
        self._bus = bus

    # ------------------------------------------------------------------ registry

    async def register(self, session: Any, *, location_context: str | None = None) -> DeviceSummary:
        self._sessions[session.device_id] = session
        summary = DeviceSummary(
            id=session.device_id,
            name=session.name,
            kind=getattr(session, "kind", "desktop"),
            os=session.os,
            status=DeviceStatus.ONLINE,
            capabilities=session.capabilities,
            last_seen_at=session.last_seen_at,
            location_context=location_context,
        )
        self._known[session.device_id] = summary
        log.info(
            "device_connected", device=session.name, os=session.os, transport=session.transport
        )
        await self._emit(
            "device.connected", session.device_id, {"name": session.name, "os": session.os}
        )
        return summary

    async def unregister(self, device_id: UUID) -> None:
        session = self._sessions.pop(device_id, None)
        if session is not None:
            await session.close()
        if (summary := self._known.get(device_id)) is not None:
            summary.status = DeviceStatus.OFFLINE
        log.info("device_disconnected", device_id=str(device_id))
        await self._emit("device.disconnected", device_id, {})

    def get(self, device_id: UUID) -> Any | None:
        return self._sessions.get(device_id)

    def online(self) -> list[DeviceSummary]:
        return [s for s in self._known.values() if s.status is DeviceStatus.ONLINE]

    def all(self) -> list[DeviceSummary]:
        return list(self._known.values())

    def summary(self, device_id: UUID) -> DeviceSummary | None:
        return self._known.get(device_id)

    def find_by_name(self, name: str) -> DeviceSummary | None:
        lowered = name.strip().lower()
        for summary in self._known.values():
            if summary.name.lower() == lowered:
                return summary
        for summary in self._known.values():
            if lowered in summary.name.lower():
                return summary
        return None

    # ------------------------------------------------------------------ dispatch

    async def invoke(self, device_id: UUID, action: DeviceAction) -> DeviceActionResult:
        session = self._sessions.get(device_id)
        if session is None:
            summary = self._known.get(device_id)
            raise DeviceUnavailable(
                f"{summary.name if summary else 'that device'} is not connected",
                device_id=str(device_id),
            )
        spec = catalogue.get(action.action)
        if spec is not None and not session.capabilities.supports(spec.capability):
            # Refuse here, before dispatch: an unsupported action must not look like a failure.
            raise DeviceActionFailed(
                f"{session.name} does not support {action.action!r}",
                device=session.name,
                capability=spec.capability,
            )

        started = time.perf_counter()
        result = await session.invoke(action)
        log.info(
            "device_action",
            device=session.name,
            action=action.action,
            ok=result.ok,
            verified=result.verified,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        await self._emit(
            "device.action_completed",
            device_id,
            {"action": action.action, "ok": result.ok, "verified": result.verified},
            task_id=action.task_id,
        )
        return result

    async def enrol(
        self,
        *,
        device_id: UUID,
        name: str,
        kind: str,
        os: str,
        capabilities: DeviceCapabilities,
    ) -> DeviceSummary:
        """Record a device the owner has paired, without claiming it can be reached.

        Enrolment and connection are different facts. A node that has registered but is not
        holding a socket has no way to receive a command, so it is recorded OFFLINE — a
        device listed as reachable that cannot act would have the router select it and fail
        three steps later, instead of saying so up front.
        """
        existing = self._known.get(device_id)
        summary = DeviceSummary(
            id=device_id,
            name=name,
            kind=kind,
            os=os,
            status=DeviceStatus.ONLINE if device_id in self._sessions else DeviceStatus.OFFLINE,
            capabilities=capabilities,
            last_seen_at=datetime.now(UTC),
            location_context=existing.location_context if existing else None,
        )
        self._known[device_id] = summary
        log.info("device_enrolled", device=name, os=os, connected=device_id in self._sessions)
        await self._emit("device.enrolled", device_id, {"name": name, "os": os})
        return summary

    async def heartbeat(self, device_id: UUID, telemetry: DeviceTelemetry) -> None:
        summary = self._known.get(device_id)
        if summary is None:
            return
        summary.telemetry = telemetry
        summary.last_seen_at = datetime.now(UTC)
        summary.status = DeviceStatus.ONLINE

    async def disconnect_all(self, *, reason: str = "emergency stop") -> int:
        """§69 — part of the lockdown path."""
        device_ids = list(self._sessions)
        for device_id in device_ids:
            await self.unregister(device_id)
        log.warning("all_devices_disconnected", count=len(device_ids), reason=reason)
        return len(device_ids)

    async def _emit(
        self, kind: str, device_id: UUID, payload: dict, *, task_id: UUID | None = None
    ) -> None:
        if self._bus is None:
            return
        await self._bus.publish(  # type: ignore[attr-defined]
            Event(kind=kind, device_id=device_id, task_id=task_id, payload=payload)
        )


def hello_from_frame(raw: str) -> Hello:
    frame = parse_frame(raw)
    if not isinstance(frame, Hello):
        raise ValueError(f"expected HELLO, got {type(frame).__name__}")
    return frame


def new_device_id() -> UUID:
    return new_id()
