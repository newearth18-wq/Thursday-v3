"""Pydantic contracts.

Anything that crosses a process, agent, or device boundary is defined here, so that both
sides of the boundary validate against the same shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from thursday_shared.enums import (
    AgentVerdict,
    ApprovalScope,
    ApprovalState,
    ControlTier,
    DataSensitivity,
    DeviceStatus,
    IntentKind,
    MemoryDecision,
    MemoryLayer,
    MemoryRelation,
    MemorySource,
    ModelTier,
    NotificationPriority,
    PermissionLevel,
    PolicyDecision,
    RiskLevel,
    StepKind,
    TaskState,
    TrustLevel,
    VoiceMode,
)
from thursday_shared.ids import current_trace_id, new_id


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
    """PART 27. What a node can actually do, advertised at HELLO.

    Capabilities are namespaced like the commands they authorise, so a node can advertise
    ``file`` without enumerating seven verbs, and the hub can still refuse ``file.delete``
    on a node that granted only ``file.read``. Lookup walks the prefixes: ``file.folder.create``
    is satisfied by ``file.folder.create``, ``file.folder`` or ``file``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    #: The set a node advertises. Everything else is derived from it.
    granted: set[str] = Field(default_factory=set)

    @classmethod
    def of(cls, *capabilities: str) -> DeviceCapabilities:
        return cls(granted=set(capabilities))

    def supports(self, capability: str) -> bool:
        parts = capability.split(".")
        return any(".".join(parts[: i + 1]) in self.granted for i in range(len(parts)))

    def grant(self, *capabilities: str) -> DeviceCapabilities:
        return DeviceCapabilities(granted=self.granted | set(capabilities))

    def revoke(self, *capabilities: str) -> DeviceCapabilities:
        return DeviceCapabilities(granted=self.granted - set(capabilities))

    def as_flags(self) -> dict[str, bool]:
        """A flat view for UIs that want checkboxes rather than a tree."""
        return dict.fromkeys(sorted(self.granted), True)


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
    """One machine, as the core sees it (§9 device identity, V8)."""

    id: UUID
    name: str
    kind: str
    os: str
    status: DeviceStatus
    capabilities: DeviceCapabilities
    telemetry: DeviceTelemetry | None = None
    last_seen_at: datetime | None = None
    location_context: str | None = None
    #: How far this device is trusted to drive *other* devices. Not how far Thursday is
    #: trusted to act on it — see `TrustLevel`.
    trust_level: TrustLevel = TrustLevel.LIMITED
    #: True when the link to this node cannot be read in transit: TLS, or a node running
    #: inside this process. A remote command over a readable link is refused.
    encrypted: bool = True
    #: What the machine is doing, for routing and for "what is the PC up to?". Distinct
    #: from telemetry's `active_app`: this is the last thing *Thursday* did there, which is
    #: the half the owner is asking about when they ask Thursday.
    current_app: str | None = None
    current_task_id: UUID | None = None

    @property
    def may_command_others(self) -> bool:
        return self.trust_level >= TrustLevel.TRUSTED


class DeviceAction(Base):
    """One instruction sent to a node. ``verify`` is not optional in spirit (§20)."""

    id: UUID = Field(default_factory=new_id)
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = 30.0
    task_id: UUID | None = None
    step_id: UUID | None = None
    reason: str = ""
    #: The machine the instruction came *from*. When it differs from the machine the action
    #: is being sent to, this is a remote command and the hub gates it (V8). None means the
    #: origin is unknown, which is itself a reason to refuse a cross-device action.
    origin_device_id: UUID | None = None
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
    #: Which conversation produced this. Lets "don't remember this" find what
    #: "this" refers to, which is the exchange that just happened.
    session_id: UUID | None = None
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
    #: Which conversation produced this. Lets "don't remember this" find what
    #: "this" refers to, which is the exchange that just happened.
    session_id: UUID | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    pinned: bool = False
    expires_at: datetime | None = None


class MemoryCandidate(Base):
    """PART 39. A proposal to remember something — not yet a memory.

    ``reason_to_store`` is required in spirit: if nothing can be said about why this is
    worth keeping, that is itself the answer.
    """

    content: str
    layer: MemoryLayer = MemoryLayer.SEMANTIC
    key: str | None = None
    structured: dict[str, Any] = Field(default_factory=dict)
    importance: float = 0.5
    confidence: float = 0.7
    source: MemorySource = MemorySource.INFERENCE
    source_ref: str | None = None
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    project_id: UUID | None = None
    task_id: UUID | None = None
    session_id: UUID | None = None
    reason_to_store: str = ""
    #: Set when an agent proposes the write, so PART 76's rule can be applied.
    proposed_by: str | None = None
    pinned: bool = False
    expires_at: datetime | None = None

    def to_write(self) -> MemoryWrite:
        return MemoryWrite(
            layer=self.layer,
            content=self.content,
            key=self.key,
            structured=self.structured,
            importance=self.importance,
            confidence=self.confidence,
            source=self.source,
            source_ref=self.source_ref,
            project_id=self.project_id,
            task_id=self.task_id,
            session_id=self.session_id,
            sensitivity=self.sensitivity,
            pinned=self.pinned,
            expires_at=self.expires_at,
        )


class MemoryJudgement(Base):
    """The write policy's answer, with its reasoning attached (PART 39)."""

    decision: MemoryDecision
    reason: str
    ttl_hours: float | None = None

    @property
    def stores(self) -> bool:
        return self.decision in (MemoryDecision.STORE, MemoryDecision.TEMPORARY)


class MemoryLink(Base):
    """PART 41. A typed edge between two memories, kept instead of an overwrite."""

    id: UUID = Field(default_factory=new_id)
    from_id: UUID
    to_id: UUID
    relation: MemoryRelation
    detected_at: datetime = Field(default_factory=utcnow)
    note: str = ""


class MemoryQuery(Base):
    text: str = ""
    layers: list[MemoryLayer] = Field(default_factory=list)
    #: Hard filter: only memories belonging to this project are considered at all.
    project_id: UUID | None = None
    #: Soft hint: memories from this project rank *higher*, but others still surface.
    #: The distinction matters — "how do I usually write these reports" should prefer
    #: this project's answer without pretending the general one does not exist.
    prefer_project_id: UUID | None = None
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
    #: The machine this step actually ran on, filled in by the orchestrator once the router
    #: has decided. The hint is what the owner said; this is what happened.
    resolved_device: str | None = None
    #: True when the device was inherited from the conversation rather than named in the
    #: sentence — the reply then has to say where the work went (see `thursday_core.focus`).
    device_announced: bool = False
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
    #: Remembered instructions this plan is following — "these reports start with a
    #: summary table". Recorded on the plan, not just injected into a prompt, so the owner
    #: can see *why* the output looks the way it does and correct the memory rather than
    #: the output (§7).
    following: list[str] = Field(default_factory=list)

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
    """PART 16's ``ToolDefinition``. Every field is used by the Tool Router or the
    Permission Engine — none of it is decoration."""

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
    #: PART 61 — a high-impact tool must be able to report what it *would* do first.
    supports_dry_run: bool = False
    #: PART 60 — a reversible tool registers how to reverse itself.
    supports_undo: bool = False


class ToolCall(Base):
    id: UUID = Field(default_factory=new_id)
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    task_id: UUID | None = None
    step_id: UUID | None = None
    device_id: UUID | None = None
    #: Where the instruction came from, as opposed to ``device_id``, which is where it is
    #: going. When they differ this is a remote command (V8) and the hub gates it.
    origin_device_id: UUID | None = None
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
    """PART 14's ``AgentJob``. No agent runs without one.

    The contract is what makes an agent's output judgeable: the success criteria are fixed
    *before* the work starts, so the Supervisor is checking against a standard rather than
    against whatever the agent decided to produce.
    """

    job_id: UUID = Field(default_factory=new_id)
    task_id: UUID
    step_id: UUID
    agent: str
    #: What to achieve. The agent's own prompt says how.
    objective: str
    instructions: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    permissions: PermissionSet = Field(default_factory=PermissionSet)
    deadline_s: float = 120.0
    budget: Budget = Field(default_factory=Budget)
    #: Populated on an informed retry — the Supervisor's critique of the last attempt.
    critique: str | None = None
    trace_id: str = Field(default_factory=current_trace_id)


#: PART 14's ``AgentJob`` is this contract; the alias keeps the brief's vocabulary usable.
AgentJob = JobContract


class AgentSpec(Base):
    """PART 11. What an agent declares about itself, so the Router can choose without
    the owner ever being asked to."""

    name: str
    description: str
    agent_type: str = "specialist"
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    supported_input: list[str] = Field(default_factory=lambda: ["text"])
    supported_output: list[str] = Field(default_factory=lambda: ["text"])
    permission_ceiling: PermissionLevel = PermissionLevel.READ
    default_budget: Budget = Field(default_factory=Budget)
    model_tier: ModelTier = ModelTier.STANDARD
    #: Rough cost per run, for the Router's cost term. Not billing.
    cost_profile: Literal["free", "cheap", "moderate", "expensive"] = "cheap"
    latency_profile: Literal["instant", "fast", "moderate", "slow"] = "fast"
    #: LOCAL_ONLY means this agent must never see cloud-routed content.
    privacy_profile: Literal["local_only", "local_preferred", "any"] = "any"
    temporary: bool = False
    system_prompt: str = ""


class AgentSelection(Base):
    """PART 13. Which agent, and *why* — a routing decision that cannot be explained is a
    routing decision that cannot be debugged."""

    agent: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    runners_up: list[tuple[str, float]] = Field(default_factory=list)
    #: Below the registry's floor, Thursday asks a clarifying question instead of guessing.
    confident: bool = True


class AgentResult(Base):
    """PART 14. What an agent hands back — including how sure it is, and what it did."""

    agent: str
    ok: bool
    job_id: UUID | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    #: What the claim rests on. The Supervisor checks provenance against this.
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    #: Every action the agent took, in order — the audit trail's raw material.
    actions_taken: list[str] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    confidence: float = 0.8
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0
    spend: Spend = Field(default_factory=Spend)

    @property
    def status(self) -> str:
        if not self.ok:
            return "failed"
        return "ok" if all(t.verified for t in self.tool_results) else "unverified"


class VerificationReport(Base):
    """PART 15's ``SupervisorResult``. ``verdict`` gates the word "success".

    ``quality_score`` is deliberately separate from ``verdict``: work can pass every check
    and still be mediocre, and a caller deciding whether to re-run wants to know which.
    """

    verdict: AgentVerdict
    checks: list[dict[str, Any]] = Field(default_factory=list)
    critique: str = ""
    reason: str = ""
    confidence: float = 0.5
    #: 0–1. How good the work is, given that it passed.
    quality_score: float = 0.0
    issues: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict is AgentVerdict.PASS

    def failures(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c.get("ok", False)]


#: PART 15's name for the same object.
SupervisorResult = VerificationReport


# --------------------------------------------------------------------------- permissions


class ActionRequest(Base):
    """What the Permission Engine judges."""

    action: str
    resource: str = ""
    device_id: UUID | None = None
    #: The machine the instruction came from, when it is not the one it will run on.
    origin_device_id: UUID | None = None
    agent: str | None = None
    level: PermissionLevel = PermissionLevel.READ
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    object_count: int = 1
    sensitivity: DataSensitivity = DataSensitivity.PRIVATE
    task_id: UUID | None = None
    step_id: UUID | None = None
    expected_outcome: str = ""

    @property
    def is_remote(self) -> bool:
        """True when this instruction crosses from one machine to another (V8)."""
        return (
            self.origin_device_id is not None
            and self.device_id is not None
            and self.origin_device_id != self.device_id
        )


class PermissionVerdict(Base):
    decision: PolicyDecision
    reason: str
    rule: str
    level: PermissionLevel
    risk: RiskLevel
    grant_id: UUID | None = None
    #: PART 21 — modifying an existing document is automatic *with a version backup*.
    requires_backup: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.AUTO

    @property
    def needs_approval(self) -> bool:
        return self.decision.requires_approval


class PermissionGrant(Base):
    """A scoped, expiring 'always allow' (PART 20).

    There is no global grant, and an ASK_ALWAYS action can never produce one.
    """

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
    #: The policy that produced this request. ASK_ALWAYS never offers "always allow".
    policy: PolicyDecision = PolicyDecision.ASK_ONCE
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    decided_at: datetime | None = None
    note: str | None = None

    @property
    def scopes_offered(self) -> list[ApprovalScope]:
        """Which answers the UI may present. Never invent ALWAYS for an ASK_ALWAYS action."""
        if self.policy.grantable:
            return [ApprovalScope.ONCE, ApprovalScope.SESSION, ApprovalScope.ALWAYS]
        return [ApprovalScope.ONCE]


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


class Attachment(Base):
    """A file the owner handed to Thursday with the request."""

    name: str
    media_type: str = "application/octet-stream"
    path: str | None = None
    size: int | None = None


class UserRequest(Base):
    """PART 6. One input model for every modality.

    Everything the owner can put in front of Thursday arrives here — typed text, a
    transcript, a camera frame, what is on screen, where they are pointing. Growing this
    by adding a field beats growing ``handle_turn(**kwargs)`` by adding a keyword, because
    the API boundary and the multimodal resolver both need the same shape.
    """

    user_id: UUID | None = None
    device_id: UUID | None = None
    conversation_id: UUID = Field(default_factory=new_id)
    text: str = ""
    #: Raw audio, when the caller has not transcribed it. STT runs core-side.
    audio: bytes | None = None
    #: A camera frame or an image the owner attached.
    image: bytes | None = None
    screen_context: ScreenContext | None = None
    selection_context: SelectionContext | None = None
    gesture_context: GestureContext | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    modality: Literal["text", "voice", "vision", "gesture", "event"] = "text"
    #: Block until an approval is answered, rather than returning the request to the caller.
    wait_for_approval: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_multimodal(self) -> bool:
        return any((self.image, self.screen_context, self.gesture_context, self.attachments))


class UiEvent(Base):
    """Something the interface should do in response — highlight, open a panel, animate."""

    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class SpeechDirective(Base):
    """How the reply should be spoken, and where. Audio is synthesised at the edge."""

    text: str
    voice_mode: VoiceMode = VoiceMode.NORMAL
    voice: str = "thursday-neutral"
    device_id: UUID | None = None
    interruptible: bool = True


class ThursdayResponse(Base):
    """PART 6. What the owner receives, whatever surface they are on."""

    text: str
    speech: SpeechDirective | None = None
    task_id: UUID | None = None
    status: TaskState | None = None
    conversation_id: UUID | None = None
    voice_mode: VoiceMode = VoiceMode.NORMAL
    avatar_state: str = "IDLE"
    confidence: float = 1.0
    #: False when an action was dispatched but its effect could not be observed (PART 5.1).
    verified: bool = True
    detail: str | None = None
    intent: Intent | None = None
    citations: list[Citation] = Field(default_factory=list)
    approvals: list[ApprovalRequest] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    ui_events: list[UiEvent] = Field(default_factory=list)
    trace_id: str = Field(default_factory=current_trace_id)

    @classmethod
    def from_reply(
        cls,
        reply: ThursdayReply,
        *,
        conversation_id: UUID | None = None,
        status: TaskState | None = None,
        voice: str = "thursday-neutral",
        device_id: UUID | None = None,
    ) -> ThursdayResponse:
        return cls(
            text=reply.text,
            speech=SpeechDirective(
                text=reply.text, voice_mode=reply.voice_mode, voice=voice, device_id=device_id
            ),
            task_id=reply.task_id,
            status=status,
            conversation_id=conversation_id,
            voice_mode=reply.voice_mode,
            avatar_state=reply.avatar_state,
            confidence=reply.confidence,
            verified=reply.verified,
            detail=reply.detail,
            intent=reply.intent,
            citations=reply.citations,
            approvals=reply.approvals,
            ui_events=[
                UiEvent(kind="avatar.state", payload={"state": reply.avatar_state}),
                *(
                    [UiEvent(kind="approval.required", payload={"count": len(reply.approvals)})]
                    if reply.approvals
                    else []
                ),
            ],
            trace_id=reply.trace_id,
        )


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
