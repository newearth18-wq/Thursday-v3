"""SQLAlchemy models — the schema from docs/04-database-schema.md.

Notes on two deliberate choices:

* ``audit_logs`` is append-only and hash-chained. In production the application role is
  granted INSERT and SELECT only; the chain makes deletion detectable even so (T10).
* ``memories`` keeps supersession as a link, not an overwrite. Thursday must be able to say
  "this used to be X, and here is why it changed" (§11).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from thursday_shared.db.base import GUID, Base, IdMixin, JSONColumn, TimestampMixin, Vector

EMBEDDING_DIMENSIONS = 768


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    locale: Mapped[str] = mapped_column(String(16), default="th-TH")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok")
    proactivity_level: Mapped[int] = mapped_column(Integer, default=2)
    voice_profile: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)

    devices: Mapped[list[Device]] = relationship(back_populates="user")


class Device(Base, IdMixin, TimestampMixin):
    __tablename__ = "devices"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32), default="desktop")
    os: Mapped[str] = mapped_column(String(64), default="")
    os_version: Mapped[str] = mapped_column(String(64), default="")
    node_version: Mapped[str] = mapped_column(String(32), default="")
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Ed25519 public key, bound at enrolment. The private half never leaves the machine.
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="offline", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    telemetry: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    location_context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trust_level: Mapped[int] = mapped_column(Integer, default=1)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="devices")

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_device_name_per_user"),)


class Goal(Base, IdMixin, TimestampMixin):
    __tablename__ = "goals"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="active")
    horizon: Mapped[str] = mapped_column(String(24), default="mission")  # goal|mission
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("goals.id"), nullable=True
    )
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base, IdMixin, TimestampMixin):
    __tablename__ = "projects"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("goals.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    vault_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    blocked_on: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONColumn, default=dict)


class Task(Base, IdMixin, TimestampMixin):
    __tablename__ = "tasks"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("projects.id"), nullable=True, index=True
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id"), nullable=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("goals.id"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)

    title: Mapped[str] = mapped_column(String(200))
    objective: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="NEW", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    #: Nullable because "no plan yet" is a real state the domain model expresses as None.
    #: Storing `{}` instead would round-trip as an *empty plan*, which is a different claim:
    #: one says nobody has planned this, the other says somebody planned it and it needs no
    #: steps (ADR 0039).
    plan: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    assigned_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    target_device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    budget: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    spent: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: PART 97 — the autonomy in force when this ran, so an audit can answer "why was this
    #: not asked about?" without guessing at the setting's history.
    autonomy_level: Mapped[int] = mapped_column(Integer, default=2)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The Supervisor's report. A task is COMPLETED only with a passing one (§76).
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), default="", index=True)

    __table_args__ = (Index("ix_tasks_user_status", "user_id", "status"),)


class TaskStep(Base, IdMixin, TimestampMixin):
    __tablename__ = "task_steps"

    task_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("tasks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="agent")
    name: Mapped[str] = mapped_column(String(64), default="")
    contract: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    depends_on: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="NEW")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    input: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Agent(Base, IdMixin, TimestampMixin):
    """PART 4/92. What an agent declares about itself, so the Router can choose.

    The row is the registry's persistent half: the code defines behaviour, this defines
    what the owner has enabled and what ceiling they set.
    """

    __tablename__ = "agents"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    agent_type: Mapped[str] = mapped_column(String(32), default="specialist")
    description: Mapped[str] = mapped_column(Text, default="")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    allowed_tools: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    #: Never exceeded, whatever a task or a dynamic agent asks for.
    permission_ceiling: Mapped[int] = mapped_column(Integer, default=0)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_agent_name_per_user"),)


class Tool(Base, IdMixin, TimestampMixin):
    """PART 4/92/16. A tool's declared risk profile, so the permission panel can show the
    owner what exists before any of it has run."""

    __tablename__ = "tools"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    permission_level: Mapped[int] = mapped_column(Integer, default=0)
    risk_level: Mapped[str] = mapped_column(String(16), default="LOW")
    #: AUTO | ASK_ONCE | ASK_ALWAYS | BLOCK — the owner may loosen this, within limits.
    approval_policy: Mapped[str] = mapped_column(String(16), default="ASK_ONCE")
    supports_dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_undo: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tool_name_per_user"),)


class AgentRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id"), nullable=True, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("task_steps.id"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[str] = mapped_column(String(32), default="1")
    contract: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), default="", index=True)


class ToolRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "tool_runs"

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("tasks.id"), nullable=True, index=True
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("task_steps.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    #: Redacted projections — raw arguments may carry credentials and are never persisted.
    args_summary: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    risk: Mapped[str] = mapped_column(String(16), default="LOW")
    permission_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    undo: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), default="", index=True)


class Approval(Base, IdMixin, TimestampMixin):
    __tablename__ = "approvals"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("tasks.id"), nullable=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    resource: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    level: Mapped[int] = mapped_column(Integer, default=3)
    expected_outcome: Mapped[str] = mapped_column(Text, default="")
    consequence_of_refusal: Mapped[str] = mapped_column(Text, default="")
    reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    dry_run: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    scope: Mapped[str] = mapped_column(String(16), default="once")
    #: ASK_ONCE or ASK_ALWAYS. An ASK_ALWAYS approval can never have produced a grant.
    policy: Mapped[str] = mapped_column(String(16), default="ASK_ONCE")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PermissionGrantRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "permission_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_glob: Mapped[str] = mapped_column(String(500), default="*")
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), default="once")
    #: Grants always expire. There is no permanent, unscoped "allow" in this system (§8.4).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    uses_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Memory(Base, IdMixin, TimestampMixin):
    __tablename__ = "memories"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    layer: Mapped[str] = mapped_column(String(24), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("projects.id"), nullable=True, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("tasks.id"), nullable=True)
    #: Which conversation produced this. What "don't remember this" searches on — without
    #: it persisted, that command would work in memory and quietly stop working the moment
    #: the SQL repositories land.
    session_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text)
    structured: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, default=0.7)
    source: Mapped[str] = mapped_column(String(24), default="inference")
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    sensitivity: Mapped[int] = mapped_column(Integer, default=2)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    __table_args__ = (Index("ix_memories_user_layer", "user_id", "layer"),)


class MemoryConflictRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "memory_conflicts"

    memory_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("memories.id"), index=True)
    key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    old_value: Mapped[str] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text)
    old_source: Mapped[str] = mapped_column(String(24))
    new_source: Mapped[str] = mapped_column(String(24))
    old_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    new_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    old_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(24), nullable=True)


class MemoryRelationRow(Base, IdMixin):
    """PART 41. How two memories relate — kept instead of overwriting one with the other."""

    __tablename__ = "memory_relations"

    from_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("memories.id"), index=True)
    to_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("memories.id"), index=True)
    relation: Mapped[str] = mapped_column(String(24), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DeviceCredential(Base, IdMixin, TimestampMixin):
    """Device pairing material (PART 26). The private half never leaves the device; this is
    the public half plus the rotation state."""

    __tablename__ = "device_credentials"

    device_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("devices.id"), index=True)
    public_key: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(String(32), default="ed25519")
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the owner revokes the device; a revoked credential is refused at HELLO.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotates_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Entity(Base, IdMixin, TimestampMixin):
    __tablename__ = "entities"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    aliases: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )


class Relationship(Base, IdMixin, TimestampMixin):
    __tablename__ = "relationships"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    src_entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("entities.id"), index=True)
    dst_entity_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("entities.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(24), default="inference")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base, IdMixin, TimestampMixin):
    __tablename__ = "documents"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("projects.id"), nullable=True
    )
    path: Mapped[str] = mapped_column(Text, index=True)
    vault_rel_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(32), default="file")
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Skill(Base, IdMixin, TimestampMixin):
    __tablename__ = "skills"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    owner: Mapped[str] = mapped_column(String(64), default="user")
    risk: Mapped[str] = mapped_column(String(16), default="LOW")
    tags: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)

    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_skill_slug_per_user"),)


class SkillVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "skill_versions"

    skill_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("skills.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    steps: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    tools: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    tests: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    changelog: Mapped[str] = mapped_column(Text, default="")
    #: A skill with destructive steps stays inert until a human approves it (§51, §96).
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("skill_id", "version", name="uq_skill_version"),)


class SkillRun(Base, IdMixin, TimestampMixin):
    __tablename__ = "skill_runs"

    skill_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("skill_versions.id"), index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("tasks.id"), nullable=True)
    sandbox: Mapped[bool] = mapped_column(Boolean, default=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)


class Automation(Base, IdMixin, TimestampMixin):
    __tablename__ = "automations"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    trigger: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    actions: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    proactivity_min: Mapped[int] = mapped_column(Integer, default=2)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    #: "thursday_suggested" automations stay disabled until the owner accepts them (§49).
    created_by: Mapped[str] = mapped_column(String(24), default="user")


class EventRow(Base, IdMixin):
    __tablename__ = "events"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="core")
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("tasks.id"), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL")
    trace_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(Base, IdMixin, TimestampMixin):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    priority: Mapped[str] = mapped_column(String(16), default="NORMAL", index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("tasks.id"), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when the notification was withheld from audio because someone else was present (§67).
    suppressed_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Observation(Base, IdMixin):
    """Vision metadata only — frames are not retained by default (§25)."""

    __tablename__ = "observations"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("devices.id"), nullable=True
    )
    object_label: Mapped[str] = mapped_column(String(120), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    bbox: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    location_context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frame_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class Decision(Base, IdMixin, TimestampMixin):
    """§55 — the decision journal."""

    __tablename__ = "decisions"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("projects.id"), nullable=True
    )
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")
    alternatives: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    source: Mapped[str] = mapped_column(String(120), default="")
    impact: Mapped[str] = mapped_column(Text, default="")
    vault_path: Mapped[str | None] = mapped_column(Text, nullable=True)


class WorldStateRow(Base, TimestampMixin):
    """One row per user, updated in place. History lives in ``events``."""

    __tablename__ = "world_state"

    user_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("users.id"), primary_key=True)
    owner_status: Mapped[str] = mapped_column(String(16), default="available")
    active_device_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    active_app: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active_project_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    active_task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    online_devices: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    running_agents: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    pending_approvals: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    location_context: Mapped[str | None] = mapped_column(String(64), nullable=True)
    open_files: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    recent_actions: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)


class ModelSpend(Base, IdMixin):
    """One model call's cost (§61, Sprint 45).

    Its own table rather than a column on ``agent_runs``, and the grain is the reason: the
    two calls every conversational turn makes — the reasoning pass that interprets the
    utterance and the supervisor pass that verifies the result — are not agent runs. Recording
    spend against agent runs is exactly the accounting that reported zero for a day of
    conversation, which is what Sprint 45 was about.
    """

    __tablename__ = "model_spend"

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), default="", index=True)
    tier: Mapped[str] = mapped_column(String(32), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    #: Attribution, not accounting: the cap is a total, and this says where it went.
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(64), default="")


class AuditLogRow(Base, IdMixin):
    """Append-only and hash-chained (§39, T10).

    In production the application role holds INSERT and SELECT only. ``prev_hash``/``hash``
    make a deletion detectable even for someone who can bypass that.
    """

    __tablename__ = "audit_logs"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(16), default="thursday")
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    #: The machine the instruction came *from*, when that differs from the one it ran on.
    #: V8 added this to `AuditEntry` and not to the table, so persisting the log would have
    #: silently dropped the one field that makes a remote command accountable: "who told my
    #: PC to do that, and from where" is unanswerable from an entry recording only the target.
    origin_device_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), default="")
    resource: Mapped[str] = mapped_column(Text, default="")
    input_summary: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONColumn, default=dict)
    result: Mapped[str] = mapped_column(String(16), default="ok")
    permission_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="", index=True)
