"""Request and response models for the HTTP API (docs/11-api-spec.md)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from thursday_shared.enums import ApprovalScope, MemoryLayer, TaskState
from thursday_shared.models import ScreenContext, SelectionContext


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
    priority: int = 5
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
