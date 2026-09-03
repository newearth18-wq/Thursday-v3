"""Device hub — the core side of TNP/1 (§9).

Holds one session per connected node, keeps the registry of what each machine can do
(§57), and refuses actions a node never advertised *before* anything is dispatched.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import DeviceStatus, TrustLevel
from thursday_shared.errors import (
    DeviceActionFailed,
    DeviceActionRefused,
    DeviceUnavailable,
)
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
    CLOSE_SESSION_ENDED,
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
    #: Nothing crosses a wire, so there is no wire to read. True by construction rather
    #: than by configuration.
    encrypted = True

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


#: Peers whose traffic never reaches a network segment anyone else can be on.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"})


def _link_is_protected(websocket: Any) -> bool:
    """Whether this connection can be read or altered by a third party in transit.

    Read from the connection itself, never from the node's own claim about itself: a node
    asserting "I am encrypted" over plaintext is precisely the case being guarded against.

    TLS qualifies. So does a plaintext socket from the loopback interface, and that is a
    deliberate exception rather than an oversight — traffic that never leaves the machine
    has no segment for anyone to sit on, and a node co-located with the core is the ordinary
    development and single-machine setup. Refusing it would mean either provisioning
    certificates to try multi-device flows locally, or turning the check off; the second is
    what people would actually do, and a check that gets turned off protects nothing.
    """
    scheme = str(getattr(getattr(websocket, "url", None), "scheme", "") or "").lower()
    if scheme in ("wss", "https"):
        return True
    host = str(getattr(getattr(websocket, "client", None), "host", "") or "").lower()
    return host in _LOOPBACK_HOSTS


class WebSocketDeviceSession:
    """A remote node. Actions are correlated by ``action_id`` so results can arrive late."""

    transport = "websocket"

    def __init__(self, websocket: Any, hello: Hello) -> None:
        self._ws = websocket
        self.encrypted = _link_is_protected(websocket)
        self.device_id = hello.device_id
        self.name = hello.name
        self.kind = hello.kind
        self.os = hello.os
        self.capabilities = hello.capabilities
        self.telemetry = hello.telemetry
        self.compute = hello.compute
        self.models = list(hello.models)
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
        """End the session *and* the connection under it.

        Closing the transport is the part that was missing, and it mattered more than it
        looks. `unregister` drops the session from the hub, which stops the core from
        dispatching to the device — but the node's socket stayed open and the handler's
        read loop kept running, so a device the owner had just **revoked** was still
        attached, still sending, and still being read. §134's emergency stop uses this same
        call to "disconnect Nodes"; without this line it disconnected nothing a node could
        notice, and a kill switch whose effect is invisible to the thing being killed is
        not a kill switch.

        Failures are suppressed rather than raised: this runs on the disconnect path too,
        where the socket is already gone and closing it again is the normal case, not an
        error worth propagating into revocation or into the emergency stop.
        """
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DeviceUnavailable("device disconnected", device=self.name))
        self._pending.clear()
        with suppress(Exception):
            await self._ws.close(code=CLOSE_SESSION_ENDED)


class DeviceHub:
    def __init__(
        self,
        bus: object | None = None,
        remote_gate: object | None = None,
        model_registry: object | None = None,
    ) -> None:
        self._sessions: dict[UUID, Any] = {}
        self._known: dict[UUID, DeviceSummary] = {}
        self._bus = bus
        #: Told what each machine holds as it connects and disconnects (ADDENDUM §5).
        #: Duck-typed and optional for the same reason the bus is: the device layer must not
        #: import the core, and a hub built in a test is not obliged to have one.
        self._model_registry = model_registry
        #: Gates instructions that cross from one machine to another. Injected rather than
        #: constructed here so the security package stays out of the device layer's imports,
        #: and duck-typed for the same reason the bus is.
        self._remote_gate = remote_gate

    # ------------------------------------------------------------------ registry

    async def register(
        self,
        session: Any,
        *,
        location_context: str | None = None,
        trust_level: TrustLevel | None = None,
    ) -> DeviceSummary:
        """Take a connected node into the registry.

        ``trust_level`` defaults to whatever the device was last enrolled with, and to
        `TrustLevel.LIMITED` for one that has never been seen. It is deliberately not read
        from the node's own HELLO: a device asserting its own trust level is a device
        granting itself permission, which is not a trust model (§9.4).
        """
        self._sessions[session.device_id] = session
        previous = self._known.get(session.device_id)
        summary = DeviceSummary(
            id=session.device_id,
            name=session.name,
            kind=getattr(session, "kind", "desktop"),
            os=session.os,
            status=DeviceStatus.ONLINE,
            capabilities=session.capabilities,
            last_seen_at=session.last_seen_at,
            location_context=location_context or (previous.location_context if previous else None),
            trust_level=(
                trust_level
                if trust_level is not None
                else (previous.trust_level if previous else TrustLevel.LIMITED)
            ),
            encrypted=bool(getattr(session, "encrypted", True)),
            # Taken from the session, which took it from HELLO (ADDENDUM §3). Read with a
            # default so a node built before this existed still registers: it reports no
            # inventory, and a machine with no inventory is never chosen to run a model,
            # which is the right answer rather than a compatibility break.
            compute=getattr(session, "compute", None),
            models=list(getattr(session, "models", []) or []),
        )
        self._known[session.device_id] = summary
        if self._model_registry is not None and summary.models:
            # Recorded at registration rather than left for the router to ask for later: a
            # machine's inventory is knowable exactly when it connects, and a registry that
            # learned it lazily would be empty for the first request after a restart, which
            # is the request most likely to be routed wrongly.
            await self._model_registry.observe(  # type: ignore[attr-defined]
                session.device_id, summary.models, online=True
            )
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
        if self._model_registry is not None:
            # Unreachable, not uninstalled. The models stay in the registry with their
            # corrections; only `online` goes false, so the router stops choosing them
            # without the owner losing what they told Thursday about them.
            await self._model_registry.device_offline(device_id)  # type: ignore[attr-defined]
        if (summary := self._known.get(device_id)) is not None:
            summary.status = DeviceStatus.OFFLINE
        log.info("device_disconnected", device_id=str(device_id))
        await self._emit("device.disconnected", device_id, {})

    async def forget(self, device_id: UUID) -> bool:
        """Drop a device from the known set entirely, not merely mark it offline.

        Distinct from :meth:`unregister`, which is what a disconnect does — an offline
        device is one that is coming back, and keeping its summary is how the owner sees
        that their laptop is asleep rather than gone. A **revoked** device is not coming
        back: it re-pairs under a new identity, so leaving the old summary listed would
        accumulate ghosts, keep a trust level nobody re-granted, and tell an operator
        reading `GET /devices` that a machine they deliberately cut off is merely away.
        """
        await self.unregister(device_id)
        return self._known.pop(device_id, None) is not None

    def get(self, device_id: UUID) -> Any | None:
        return self._sessions.get(device_id)

    def online(self) -> list[DeviceSummary]:
        return [s for s in self._known.values() if s.status is DeviceStatus.ONLINE]

    def all(self) -> list[DeviceSummary]:
        return list(self._known.values())

    def summary(self, device_id: UUID) -> DeviceSummary | None:
        return self._known.get(device_id)

    def set_trust(self, device_id: UUID, level: TrustLevel) -> DeviceSummary | None:
        """Change how far a device is trusted to drive others. The owner's call, only."""
        summary = self._known.get(device_id)
        if summary is None:
            return None
        before = summary.trust_level
        summary.trust_level = level
        log.info("device_trust_changed", device=summary.name, before=str(before), after=str(level))
        return summary

    def note_activity(
        self, device_id: UUID, *, app: str | None = None, task_id: UUID | None = None
    ) -> None:
        """Record what Thursday is doing on a machine — the presence half of §9 identity."""
        summary = self._known.get(device_id)
        if summary is None:
            return
        if app is not None:
            summary.current_app = app
        summary.current_task_id = task_id
        summary.last_seen_at = datetime.now(UTC)

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

        verdict = self._check_remote(action, device_id)
        if verdict is not None and not verdict.allowed:
            # Refused before dispatch, like an unsupported capability: nothing has happened
            # on the target machine, and the caller learns why rather than seeing a failure.
            raise DeviceActionRefused(
                verdict.reason,
                device=session.name,
                action=action.action,
            )

        started = time.perf_counter()
        result = await session.invoke(action)
        origin = self._known.get(action.origin_device_id) if action.origin_device_id else None
        log.info(
            "device_action",
            device=session.name,
            # Both ends, always. "Who told my PC to do that, and from where" is not
            # answerable afterwards from a log line that records only the target.
            origin=origin.name if origin else None,
            remote=bool(origin and origin.id != device_id),
            action=action.action,
            ok=result.ok,
            verified=result.verified,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        await self._emit(
            "device.action_completed",
            device_id,
            {
                "action": action.action,
                "ok": result.ok,
                "verified": result.verified,
                "origin_device_id": str(action.origin_device_id)
                if action.origin_device_id
                else None,
            },
            task_id=action.task_id,
        )
        return result

    def _check_remote(self, action: DeviceAction, device_id: UUID) -> Any:
        """Ask the remote gate whether this instruction may cross machines.

        Only refusals come back from here. Whether an allowed remote action *also* needs
        the owner's approval was already decided upstream by the permission engine, which
        saw the same `origin_device_id` on the `ActionRequest`.
        """
        if self._remote_gate is None:
            return None
        target = self._known.get(device_id)
        if target is None:
            return None
        origin = self._known.get(action.origin_device_id) if action.origin_device_id else None
        return self._remote_gate.check(  # type: ignore[attr-defined]
            action=action.action,
            origin=origin,
            target=target,
            origin_device_id=action.origin_device_id,
        )

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

    async def heartbeat(
        self, device_id: UUID, telemetry: DeviceTelemetry, *, load: Any = None
    ) -> None:
        summary = self._known.get(device_id)
        if summary is None:
            return
        summary.telemetry = telemetry
        summary.last_seen_at = datetime.now(UTC)
        summary.status = DeviceStatus.ONLINE
        if load is not None:
            # §18. Only when reported: keeping the last known load beats replacing it with
            # zeros, which would read as "this machine is idle" and attract exactly the work
            # it should not get.
            summary.load = load

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
