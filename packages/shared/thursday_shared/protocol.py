"""TNP/1 — the Thursday Node Protocol (§9).

JSON over WebSocket, dialled *outbound* by the node so no machine has to expose a port.
Both core and node validate against these models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from thursday_shared.ids import new_id
from thursday_shared.models import DeviceCapabilities, DeviceTelemetry, UndoRecord, utcnow

PROTOCOL_VERSION = 1

#: WebSocket close codes in the private range (4000–4999), so a node can tell *why* it was
#: dropped without parsing a message it may never receive. The distinction that matters is
#: between "come back" and "do not": a node that reconnects after being revoked is a node
#: hammering a core that will refuse it for ever, and a node that gives up after a routine
#: session expiry is a machine the owner has silently lost.
CLOSE_PROTOCOL_ERROR = 4400
#: HELLO failed authentication. Terminal for this identity: reconnecting changes nothing.
CLOSE_UNAUTHENTICATED = 4401
#: The session reached its maximum age (§79). Expected, routine, and the node should
#: reconnect immediately with a fresh signed HELLO.
CLOSE_SESSION_EXPIRED = 4408
#: The core ended this session — a revocation, or §134's emergency stop. Not an invitation
#: to reconnect; whether the node may return is decided by whether its credential still
#: authenticates, which the core will answer at the next HELLO.
CLOSE_SESSION_ENDED = 4409


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    id: UUID = Field(default_factory=new_id)
    ts: datetime = Field(default_factory=utcnow)


class Hello(Frame):
    type: Literal["HELLO"] = "HELLO"
    device_id: UUID
    name: str
    kind: str = "desktop"
    os: str
    os_version: str = ""
    node_version: str = "0.1.0"
    capabilities: DeviceCapabilities
    telemetry: DeviceTelemetry
    nonce: str
    signature: str = ""


class Welcome(Frame):
    type: Literal["WELCOME"] = "WELCOME"
    session_id: UUID
    server_time: datetime = Field(default_factory=utcnow)
    heartbeat_s: float = 15.0
    policy: dict[str, Any] = Field(default_factory=dict)


class Heartbeat(Frame):
    type: Literal["HEARTBEAT"] = "HEARTBEAT"
    telemetry: DeviceTelemetry


class ActionFrame(Frame):
    type: Literal["ACTION"] = "ACTION"
    action_id: UUID
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 30.0
    #: Bound to (task, action, device, expiry) so a node cannot be a confused deputy (T15).
    permission_token: str = ""
    trace_id: str = ""


class ActionResultFrame(Frame):
    type: Literal["ACTION_RESULT"] = "ACTION_RESULT"
    action_id: UUID
    ok: bool
    verified: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    undo: UndoRecord | None = None


class EventFrame(Frame):
    type: Literal["EVENT"] = "EVENT"
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CancelFrame(Frame):
    type: Literal["CANCEL"] = "CANCEL"
    action_id: UUID
    reason: str = ""


class ShutdownFrame(Frame):
    type: Literal["SHUTDOWN"] = "SHUTDOWN"
    reason: str = ""
    reconnect_after_s: float | None = None


class ErrorFrame(Frame):
    type: Literal["ERROR"] = "ERROR"
    code: str
    message: str
    fatal: bool = False


_FRAME_TYPES: dict[str, type[Frame]] = {
    "HELLO": Hello,
    "WELCOME": Welcome,
    "HEARTBEAT": Heartbeat,
    "ACTION": ActionFrame,
    "ACTION_RESULT": ActionResultFrame,
    "EVENT": EventFrame,
    "CANCEL": CancelFrame,
    "SHUTDOWN": ShutdownFrame,
    "ERROR": ErrorFrame,
}


def parse_frame(raw: str | bytes | dict[str, Any]) -> Frame:
    """Decode a wire frame, raising on an unknown type or version."""
    import json

    data: dict[str, Any] = raw if isinstance(raw, dict) else json.loads(raw)
    kind = data.get("type")
    if kind not in _FRAME_TYPES:
        raise ValueError(f"unknown frame type: {kind!r}")
    if data.get("v", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version: {data.get('v')}")
    return _FRAME_TYPES[kind].model_validate(data)


def dump_frame(frame: Frame) -> str:
    return frame.model_dump_json()
