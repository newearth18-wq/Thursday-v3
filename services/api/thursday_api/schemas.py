"""Request and response models for the HTTP API (docs/11-api-spec.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from thursday_shared.enums import ApprovalScope, MemoryLayer, Priority, TaskState
from thursday_shared.models import DeviceTelemetry, ScreenContext, SelectionContext


class ConversationRequest(BaseModel):
    text: str
    session_id: UUID | None = None
    device_id: UUID | None = None
    modality: str = "text"
    screen: ScreenContext | None = None
    selection: SelectionContext | None = None
    wait_for_approval: bool = False


class ConversationResponse(BaseModel):
    session_id: UUID
    text: str
    voice_mode: str
    avatar_state: str
    confidence: float
    #: False when an action was dispatched but its effect could not be observed.
    verified: bool
    detail: str | None = None
    task_id: UUID | None = None
    status: str | None = None
    intent: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    ui_events: list[dict[str, Any]] = Field(default_factory=list)
    speech: dict[str, Any] | None = None
    trace_id: str


class ProjectCreateRequest(BaseModel):
    name: str
    goal: str = ""
    description: str = ""


class DecisionRequest(BaseModel):
    """PART 44/55 — a decision worth remembering, with its reasoning."""

    decision: str
    reason: str = ""
    alternatives: list[str] = Field(default_factory=list)
    source: str = ""
    impact: str = ""
    project_id: UUID | None = None


class MemorySearchRequest(BaseModel):
    q: str = ""
    layer: MemoryLayer | None = None
    project_id: UUID | None = None
    k: int = 8
    min_confidence: float = 0.0


class MemoryConfirmRequest(BaseModel):
    """PART 39 — the owner's answer to an ASK_USER candidate."""

    index: int = 0
    accept: bool = True


class TaskCreateRequest(BaseModel):
    objective: str
    title: str | None = None
    project_id: UUID | None = None
    device_id: UUID | None = None
    priority: Priority = Priority.NORMAL
    budget_usd: float | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: str  # approve | reject | approve_once | always_allow
    scope: ApprovalScope | None = None
    note: str | None = None


class MemoryWriteRequest(BaseModel):
    content: str
    layer: MemoryLayer = MemoryLayer.SEMANTIC
    key: str | None = None
    importance: float = 0.5
    project_id: UUID | None = None
    structured: dict[str, Any] = Field(default_factory=dict)


class DeviceActionRequest(BaseModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class EmergencyStopRequest(BaseModel):
    scope: str = "all"  # all | agents | camera | microphone | devices | tokens


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class TaskFilter(BaseModel):
    status: TaskState | None = None
    project_id: UUID | None = None
    limit: int = 50


class DeviceRegistration(BaseModel):
    """The HTTP form of a HELLO. Signed exactly as the WebSocket handshake is."""

    device_id: UUID
    name: str
    kind: str = "desktop"
    os: str
    os_version: str = ""
    capabilities: list[str] = Field(default_factory=list)
    nonce: str
    issued_at: datetime
    signature: str


class DeviceHeartbeat(BaseModel):
    device_id: UUID
    name: str
    os: str
    telemetry: DeviceTelemetry = Field(default_factory=DeviceTelemetry)
    nonce: str
    issued_at: datetime
    signature: str


class PairingStart(BaseModel):
    """A node asking to pair. Everything here is signed by the key it offers."""

    public_key: str
    name: str
    os: str
    hostname: str = ""
    nonce: str
    issued_at: datetime
    signature: str


class PairingComplete(BaseModel):
    """The owner confirming the code the device displayed."""

    code: str
    device_type: str = "desktop"


class CredentialRotation(BaseModel):
    """A paired node replacing its own key (§117).

    Signed twice over the same payload: once by the key being retired, which is the
    authority for the request, and once by the key being introduced, which proves the node
    can actually sign with what it is asking the core to trust.
    """

    new_public_key: str
    signature_by_old: str
    signature_by_new: str
    nonce: str
    issued_at: datetime
