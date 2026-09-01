"""Pydantic contracts.

Anything that crosses a process, agent, or device boundary is defined here, so that both
sides of the boundary validate against the same shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thursday.shared.enums import (
    AgentVerdict,
    ApprovalScope,
    ApprovalState,
    ControlTier,
    DataSensitivity,
    DeviceStatus,
    IntentKind,
    MemoryLayer,
    MemorySource,
    ModelTier,
    NotificationPriority,
    PermissionLevel,
    PolicyDecision,
    RiskLevel,
    StepKind,
    TaskState,
    VoiceMode,
)
from thursday.shared.ids import current_trace_id, new_id


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------- conversation


class ConversationTurn(Base):
    id: UUID = Field(default_factory=new_id)
    session_id: UUID
    role: Literal["user", "thursday", "system"]
    text: str
    device_id: UUID | None = None
    modality: Literal["text", "voice", "vision", "gesture", "event"] = "text"
    language: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScreenContext(Base):
    """What the user is looking at (§30). Populated only with screen permission."""

    active_window: str | None = None
    active_app: str | None = None
    url: str | None = None
    visible_text: str | None = None
    elements: list[dict[str, Any]] = Field(default_factory=list)
    region: dict[str, int] | None = None


class SelectionContext(Base):
    text: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    clipboard: str | None = None


class GestureContext(Base):
    """§27–29. Only present while gesture mode is active."""

    gesture: str
    confidence: float
    pointing_at: dict[str, float] | None = None
    hands: int = 1


class Budget(Base):
    """§61. Absent limits mean 'inherit the parent's', not 'unlimited'."""

    tokens: int | None = None
    usd: float | None = None
    seconds: float | None = None
    agent_calls: int | None = None
    tool_calls: int | None = None

    def intersect(self, other: Budget) -> Budget:
        """Take the tighter of each limit — budgets never widen going down the tree."""

        def tighter(a: Any, b: Any) -> Any:
            if a is None:
                return b
            if b is None:
                return a
            return min(a, b)

        return Budget(
            tokens=tighter(self.tokens, other.tokens),
            usd=tighter(self.usd, other.usd),
            seconds=tighter(self.seconds, other.seconds),
            agent_calls=tighter(self.agent_calls, other.agent_calls),
            tool_calls=tighter(self.tool_calls, other.tool_calls),
        )


class Spend(Base):
    tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0
    agent_calls: int = 0
    tool_calls: int = 0

    def exceeds(self, budget: Budget) -> str | None:
        """Return the name of the first breached limit, or None."""
        for field in ("tokens", "usd", "seconds", "agent_calls", "tool_calls"):
            limit = getattr(budget, field)
            if limit is not None and getattr(self, field) > limit:
                return field
        return None


# --------------------------------------------------------------------------- devices


class DeviceCapabilities(Base):
    """§57. Flat map; the Device Router refuses actions a node never advertised."""

    model_config = ConfigDict(extra="allow", validate_assignment=True)

    open_app: bool = False
    close_app: bool = False
    open_file: bool = False
    write_file: bool = False
    delete_file: bool = False
    list_dir: bool = False
    search_files: bool = False
    run_shell: bool = False
    screenshot: bool = False
    read_active_window: bool = False
    clipboard: bool = False
    notify: bool = False
    volume: bool = False
    process_status: bool = False
    system_info: bool = False
    power: bool = False
    camera: bool = False
    microphone: bool = False
    speaker: bool = False
    gpu: bool = False
    browser: bool = False

    def supports(self, capability: str) -> bool:
        return bool(getattr(self, capability, False) or self.model_extra.get(capability, False))


class DeviceTelemetry(Base):
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    battery_percent: float | None = None
    charging: bool | None = None
    network: str | None = None
    active_app: str | None = None
    active_window: str | None = None
    current_user: str | None = None
    screen_locked: bool | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_free_gb: float | None = None
    reported_at: datetime = Field(default_factory=utcnow)


class DeviceSummary(Base):
    id: UUID
    name: str
    kind: str
    os: str
    status: DeviceStatus
    capabilities: DeviceCapabilities
    telemetry: DeviceTelemetry | None = None
    last_seen_at: datetime | None = None
    location_context: str | None = None


class DeviceAction(Base):
    """One instruction sent to a node. ``verify`` is not optional in spirit (§20)."""

    id: UUID = Field(default_factory=new_id)
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 30.0
    task_id: UUID | None = None
    step_id: UUID | None = None
    reason: str = ""
    trace_id: str = Field(default_factory=current_trace_id)


class UndoRecord(Base):
    """§40. Actions without one of these are treated as irreversible by the policy engine."""

    action_id: UUID
    operation: str
    args: dict[str, Any] = Field(default_factory=dict)
    device_id: UUID | None = None
    previous_state: dict[str, Any] | None = None
    expires_at: datetime | None = None
    description: str = ""


class DeviceActionResult(Base):
    action_id: UUID
    ok: bool
    verified: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    undo: UndoRecord | None = None

    @property
    def succeeded(self) -> bool:
        """Dispatch is not success. Only a verified effect counts (§20, §76)."""
        return self.ok and self.verified


# --------------------------------------------------------------------------- memory


class MemoryRecord(Base):
    id: UUID = Field(default_factory=new_id)
    layer: MemoryLayer
    key: str | None = None
    content: str
    structured: dict[str, Any] = Field(default_factory=dict)
    importance: float = 0.5
    confidence: float = 0.7
    source: MemorySource = MemorySource.INFERENCE
    source_ref: str | None = None
    project_id: UUID | None = None
    task_id: UUID | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    created_at: datetime = Field(default_factory=utcnow)
    last_accessed_at: datetime | None = None
    access_count: int = 0
    pinned: bool = False
    supersedes_id: UUID | None = None
    superseded_by_id: UUID | None = None
    valid_from: datetime = Field(default_factory=utcnow)
    valid_to: datetime | None = None
    expires_at: datetime | None = None
    embedding: list[float] | None = None
    score: float | None = None  # populated by retrieval, not stored

    @field_validator("importance", "confidence")
    @classmethod
    def _bounded(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @property
    def is_current(self) -> bool:
        return self.superseded_by_id is None and self.valid_to is None


class MemoryWrite(Base):
    layer: MemoryLayer
    content: str
    key: str | None = None
    structured: dict[str, Any] = Field(default_factory=dict)
    importance: float = 0.5
    confidence: float = 0.7
    source: MemorySource = MemorySource.INFERENCE
    source_ref: str | None = None
    project_id: UUID | None = None
    task_id: UUID | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    pinned: bool = False
    expires_at: datetime | None = None


class MemoryQuery(Base):
    text: str = ""
    layers: list[MemoryLayer] = Field(default_factory=list)
    project_id: UUID | None = None
    task_id: UUID | None = None
    key: str | None = None
    k: int = 8
    min_confidence: float = 0.0
    include_superseded: bool = False


class MemoryConflict(Base):
    """§11. A contradiction is recorded, never silently merged."""

    id: UUID = Field(default_factory=new_id)
    memory_id: UUID
    key: str | None
    old_value: str
    new_value: str
    old_source: MemorySource
    new_source: MemorySource
    old_confidence: float
    new_confidence: float
    old_observed_at: datetime
    new_observed_at: datetime
    resolution: Literal["pending", "kept_old", "kept_new", "both_valid", "user_decided"] = "pending"
    detected_at: datetime = Field(default_factory=utcnow)

    def describe(self) -> str:
        return (
            f"conflict on {self.key or 'value'}: "
            f"old={self.old_value!r} ({self.old_source}, conf {self.old_confidence:.2f}, "
            f"{self.old_observed_at:%Y-%m-%d}) vs "
            f"new={self.new_value!r} ({self.new_source}, conf {self.new_confidence:.2f}, "
            f"{self.new_observed_at:%Y-%m-%d})"
        )


# --------------------------------------------------------------------------- world state


class WorldStateSnapshot(Base):
    """§12. The 'now' Thursday reasons against — what 'this', 'that file', 'continue' mean."""

    owner_status: Literal["available", "busy", "dnd", "away", "asleep"] = "available"
    active_device_id: UUID | None = None
    active_device_name: str | None = None
    active_app: str | None = None
    active_project_id: UUID | None = None
    active_task_id: UUID | None = None
    online_devices: list[DeviceSummary] = Field(default_factory=list)
    running_agents: dict[str, str] = Field(default_factory=dict)
    pending_approvals: list[UUID] = Field(default_factory=list)
    location_context: str | None = None
    open_files: list[str] = Field(default_factory=list)
    recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    last_referenced_file: str | None = None
    last_referenced_task_id: UUID | None = None
    people_present: int = 1
    lockdown: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- context/intent


class ProjectSummary(Base):
    id: UUID
    name: str
    goal: str | None = None
    status: str = "active"
    blocked_on: list[str] = Field(default_factory=list)


class ContextPackage(Base):
    """Everything the Reasoning Engine is allowed to see for one turn (§13)."""

    turn: ConversationTurn
    history: list[ConversationTurn] = Field(default_factory=list)
    world: WorldStateSnapshot
    memories: list[MemoryRecord] = Field(default_factory=list)
    devices: list[DeviceSummary] = Field(default_factory=list)
    screen: ScreenContext | None = None
    selection: SelectionContext | None = None
    gesture: GestureContext | None = None
    project: ProjectSummary | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    budget: Budget = Field(default_factory=Budget)
    offline: bool = False
    trace_id: str = Field(default_factory=current_trace_id)


class Intent(Base):
    kind: IntentKind
    objective: str
    entities: dict[str, Any] = Field(default_factory=dict)
    target_device: str | None = None
    needs_plan: bool = False
    confidence: float = 0.5
    rationale: str = ""
    direct_answer: str | None = None


# --------------------------------------------------------------------------- plans/tasks


class PlanStep(Base):
    id: UUID = Field(default_factory=new_id)
    seq: int
    kind: StepKind
    name: str
    objective: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[UUID] = Field(default_factory=list)
    device_hint: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    max_attempts: int = 2
    status: TaskState = TaskState.NEW
    attempt: int = 0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Plan(Base):
    id: UUID = Field(default_factory=new_id)
    objective: str
    steps: list[PlanStep] = Field(default_factory=list)
    rationale: str = ""

    def ready_steps(self) -> list[PlanStep]:
        """Steps whose dependencies are all COMPLETED — the DAG frontier."""
        done = {s.id for s in self.steps if s.status is TaskState.COMPLETED}
        return [
            s
            for s in self.steps
            if s.status is TaskState.NEW and all(d in done for d in s.depends_on)
        ]


class Task(Base):
    """§41."""

    id: UUID = Field(default_factory=new_id)
    title: str
    objective: str
    status: TaskState = TaskState.NEW
    priority: int = 5
    progress: float = 0.0
    project_id: UUID | None = None
    parent_task_id: UUID | None = None
    goal_id: UUID | None = None
    session_id: UUID | None = None
    plan: Plan | None = None
    assigned_agent: str | None = None
    origin_device_id: UUID | None = None
    target_device_id: UUID | None = None
    budget: Budget = Field(default_factory=Budget)
    spent: Spend = Field(default_factory=Spend)
    deadline: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    verification: VerificationReport | None = None
    trace_id: str = Field(default_factory=current_trace_id)


# --------------------------------------------------------------------------- tools/agents


class ToolSpec(Base):
    """§32. Every field here is used by the Tool Router, not decoration."""

    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    permission: PermissionLevel = PermissionLevel.READ
    control_tier: ControlTier = ControlTier.API
    risk: RiskLevel = RiskLevel.LOW
    cost_usd: float = 0.0
    latency_ms: int = 100
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    requires_device: bool = False
    reversible: bool = True
    max_sensitivity: DataSensitivity = DataSensitivity.SECRET
    local_only: bool = False


class ToolCall(Base):
    id: UUID = Field(default_factory=new_id)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    task_id: UUID | None = None
    step_id: UUID | None = None
    device_id: UUID | None = None
    reason: str = ""


class ToolResult(Base):
    call_id: UUID
    tool: str
    ok: bool
    verified: bool = False
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    undo: UndoRecord | None = None
    cost_usd: float = 0.0


class PermissionSet(Base):
    """The permission envelope an agent runs inside. Intersection only, never union (§8.5)."""

    max_level: PermissionLevel = PermissionLevel.READ
    allowed_tools: list[str] = Field(default_factory=list)
    path_scopes: list[str] = Field(default_factory=list)
    device_ids: list[UUID] = Field(default_factory=list)
    network: bool = False

    def intersect(self, other: PermissionSet) -> PermissionSet:
        def narrow(a: list[Any], b: list[Any]) -> list[Any]:
            if not a:
                return list(b)
            if not b:
                return list(a)
            return [x for x in a if x in b]

        return PermissionSet(
            max_level=PermissionLevel(min(self.max_level, other.max_level)),
            allowed_tools=narrow(self.allowed_tools, other.allowed_tools),
            path_scopes=narrow(self.path_scopes, other.path_scopes),
            device_ids=narrow(self.device_ids, other.device_ids),
            network=self.network and other.network,
        )


class JobContract(Base):
    """§17. No agent runs without one."""

    task_id: UUID
    step_id: UUID
    agent: str
    objective: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    permissions: PermissionSet = Field(default_factory=PermissionSet)
    deadline_s: float = 120.0
    budget: Budget = Field(default_factory=Budget)
    critique: str | None = None  # populated on an informed retry (§18)
    trace_id: str = Field(default_factory=current_trace_id)


class AgentSpec(Base):
    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    permission_ceiling: PermissionLevel = PermissionLevel.READ
    default_budget: Budget = Field(default_factory=Budget)
    model_tier: ModelTier = ModelTier.STANDARD
    temporary: bool = False
    system_prompt: str = ""


class AgentResult(Base):
    agent: str
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0
    spend: Spend = Field(default_factory=Spend)


class VerificationReport(Base):
    """§18, §76. ``verdict`` gates the word 'success'."""

    verdict: AgentVerdict
    checks: list[dict[str, Any]] = Field(default_factory=list)
    critique: str = ""
    confidence: float = 0.5

    @property
    def passed(self) -> bool:
        return self.verdict is AgentVerdict.PASS

    def failures(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c.get("ok", False)]


# --------------------------------------------------------------------------- permissions


class ActionRequest(Base):
    """What the Permission Engine judges."""

    action: str
    resource: str = ""
    device_id: UUID | None = None
    agent: str | None = None
    level: PermissionLevel = PermissionLevel.READ
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    object_count: int = 1
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    task_id: UUID | None = None
    step_id: UUID | None = None
    expected_outcome: str = ""


class PermissionVerdict(Base):
    decision: PolicyDecision
    reason: str
    rule: str
    level: PermissionLevel
    risk: RiskLevel
    grant_id: UUID | None = None


class PermissionGrant(Base):
    """A scoped, expiring 'always allow' (§38). There is no global grant."""

    id: UUID = Field(default_factory=new_id)
    action: str
    resource_glob: str = "*"
    device_id: UUID | None = None
    scope: ApprovalScope = ApprovalScope.ONCE
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    uses_remaining: int | None = None

    def matches(self, req: ActionRequest, *, now: datetime | None = None) -> bool:
        from fnmatch import fnmatch

        now = now or utcnow()
        if self.expires_at and self.expires_at <= now:
            return False
        if self.uses_remaining is not None and self.uses_remaining <= 0:
            return False
        if self.action not in ("*", req.action):
            return False
        if self.device_id and req.device_id and self.device_id != req.device_id:
            return False
        return fnmatch(req.resource or "", self.resource_glob)


class ApprovalRequest(Base):
    """§38. Everything the user needs in order to decide, and nothing they must go find."""

    id: UUID = Field(default_factory=new_id)
    action: str
    agent: str | None = None
    device_id: UUID | None = None
    device_name: str | None = None
    resource: str = ""
    risk: RiskLevel = RiskLevel.MEDIUM
    level: PermissionLevel = PermissionLevel.EXTERNAL
    expected_outcome: str = ""
    consequence_of_refusal: str = ""
    reversible: bool = True
    dry_run: DryRunReport | None = None
    task_id: UUID | None = None
    step_id: UUID | None = None
    state: ApprovalState = ApprovalState.PENDING
    scope: ApprovalScope = ApprovalScope.ONCE
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    note: str | None = None


class DryRunReport(Base):
    """§72. The approval is bound to ``fingerprint``; if the world moved, it is void."""

    operation: str
    will_create: int = 0
    will_modify: int = 0
    will_move: int = 0
    will_rename: int = 0
    will_delete: int = 0
    conflicts: list[str] = Field(default_factory=list)
    samples: list[str] = Field(default_factory=list)
    fingerprint: str = ""

    def summary(self) -> str:
        parts = []
        for label, n in (
            ("create", self.will_create),
            ("modify", self.will_modify),
            ("move", self.will_move),
            ("rename", self.will_rename),
            ("delete", self.will_delete),
        ):
            if n:
                parts.append(f"{n} files will {label}")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} conflicts")
        return ", ".join(parts) or "no changes"


# --------------------------------------------------------------------------- events/output


class Event(Base):
    id: UUID = Field(default_factory=new_id)
    kind: str
    source: str = "core"
    device_id: UUID | None = None
    task_id: UUID | None = None
    session_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: NotificationPriority = NotificationPriority.NORMAL
    trace_id: str = Field(default_factory=current_trace_id)
    occurred_at: datetime = Field(default_factory=utcnow)


class Citation(Base):
    """§74 provenance for anything consequential."""

    source: MemorySource
    ref: str
    detail: str = ""
    confidence: float = 1.0


class ThursdayReply(Base):
    """What the user actually receives."""

    text: str
    voice_mode: VoiceMode = VoiceMode.NORMAL
    avatar_state: str = "IDLE"
    confidence: float = 1.0
    task_id: UUID | None = None
    intent: Intent | None = None
    citations: list[Citation] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    detail: str | None = None
    verified: bool = True
    trace_id: str = Field(default_factory=current_trace_id)


class LLMMessage(Base):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(Base):
    messages: list[LLMMessage]
    tier: ModelTier = ModelTier.STANDARD
    max_tokens: int = 1024
    temperature: float = 0.3
    stop: list[str] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE


class LLMResponse(Base):
    text: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    finish_reason: str = "stop"
    structured: dict[str, Any] | None = None


class HealthStatus(Base):
    name: str
    ok: bool
    detail: str = ""
    latency_ms: float | None = None
    checked_at: datetime = Field(default_factory=utcnow)


# Late binding: ApprovalRequest references DryRunReport, Task references VerificationReport.
ApprovalRequest.model_rebuild()
Task.model_rebuild()
