"""Skill model (§50–53).

A skill is a workflow Thursday learned and can repeat. The lifecycle exists because a
learned workflow is *code the owner did not review*:

    draft ──(sandbox tests pass)──► testing ──(owner approves)──► active
                                                                     │
                                                          v2, v3 … ──┘  rollback available

A draft never runs against real data, and a skill containing a destructive step cannot
become active without an explicit human approval (§96).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from thursday_core.undo import is_destructive
from thursday_security.policy import PolicyTable
from thursday_shared.enums import PermissionLevel, RiskLevel
from thursday_shared.ids import new_id
from thursday_shared.models import PermissionSet

#: Default policy, used to judge a captured step's risk. A skill is reviewed against the
#: same rules a live action would face, so capture cannot launder authority.
_POLICY = PolicyTable()


class SkillStatus(StrEnum):
    DRAFT = "draft"
    TESTING = "testing"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


@dataclass
class SkillStep:
    seq: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    #: A plain-language rule for when to skip or branch, captured from the demonstration.
    condition: str | None = None
    on_error: str = "stop"  # stop | continue | ask

    @property
    def destructive(self) -> bool:
        """Changes something with no way back."""
        return is_destructive(self.tool)

    @property
    def risky(self) -> bool:
        """Needs a human before a *learned* workflow may do it unattended (§51, §96).

        Reversibility is not the whole story: deleting 128 files is recoverable from the
        quarantine folder and still not something a captured workflow should start doing
        on its own. So risk and permission level count too.
        """
        policy = _POLICY.get(self.tool)
        return (
            self.destructive
            or policy.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            or policy.level >= PermissionLevel.EXTERNAL
        )


@dataclass
class SkillTest:
    """A sandbox case. Skills are proven on fixtures before they touch real data (§52)."""

    name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillVersion:
    version: int = 1
    steps: list[SkillStep] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    permissions: PermissionSet = field(default_factory=PermissionSet)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    tests: list[SkillTest] = field(default_factory=list)
    changelog: str = ""
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def risky_steps(self) -> list[SkillStep]:
        return [s for s in self.steps if s.risky]

    @property
    def destructive_steps(self) -> list[SkillStep]:
        return [s for s in self.steps if s.destructive]

    @property
    def risk(self) -> RiskLevel:
        if self.permissions.max_level >= PermissionLevel.SYSTEM:
            return RiskLevel.CRITICAL
        if self.risky_steps or self.permissions.max_level >= PermissionLevel.EXTERNAL:
            return RiskLevel.HIGH
        if self.permissions.max_level >= PermissionLevel.MODIFY:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @property
    def needs_approval(self) -> bool:
        """A skill that can destroy, spend, or reach outside may not self-activate."""
        return bool(self.risky_steps) or self.permissions.max_level >= PermissionLevel.EXTERNAL


@dataclass
class Skill:
    id: UUID = field(default_factory=new_id)
    name: str = ""
    slug: str = ""
    description: str = ""
    status: SkillStatus = SkillStatus.DRAFT
    owner: str = "user"
    tags: list[str] = field(default_factory=list)
    versions: list[SkillVersion] = field(default_factory=list)
    current_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def version(self, number: int | None = None) -> SkillVersion | None:
        target = number or self.current_version
        return next((v for v in self.versions if v.version == target), None)

    @property
    def latest(self) -> SkillVersion | None:
        return max(self.versions, key=lambda v: v.version, default=None)

    def add_version(self, version: SkillVersion) -> SkillVersion:
        version.version = (self.latest.version + 1) if self.latest else 1
        self.versions.append(version)
        return version
