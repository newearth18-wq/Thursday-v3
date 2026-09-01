"""Skill registry: capture, sandbox testing, approval, activation, rollback (§50–53)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.actions import canonical
from thursday_shared.errors import PermissionDenied, ThursdayError
from thursday_shared.models import ToolCall

from thursday_automation.skills.models import Skill, SkillStatus, SkillStep, SkillTest, SkillVersion

log = get_logger(__name__)


@dataclass
class SandboxResult:
    ok: bool
    passed: int
    failed: int
    failures: list[str]

    def summary(self) -> str:
        return f"{self.passed} passed, {self.failed} failed" + (
            f" — {'; '.join(self.failures[:3])}" if self.failures else ""
        )


class SkillRegistry:
    def __init__(self, *, executor: object | None = None, tools: object | None = None) -> None:
        self._skills: dict[UUID, Skill] = {}
        self._executor = executor
        self._tools = tools

    # ------------------------------------------------------------------ capture

    def capture(
        self,
        *,
        name: str,
        description: str,
        steps: list[SkillStep],
        permissions: Any = None,
        tests: list[SkillTest] | None = None,
    ) -> Skill:
        """Turn a demonstration into a **draft**. Drafts do not run against real data."""
        from thursday_shared.models import PermissionSet

        skill = Skill(
            name=name,
            slug=_slugify(name),
            description=description,
            status=SkillStatus.DRAFT,
        )
        version = SkillVersion(
            steps=steps,
            tools=sorted({s.tool for s in steps}),
            permissions=permissions or PermissionSet(),
            tests=tests or [],
            changelog="captured from demonstration",
        )
        skill.add_version(version)
        skill.current_version = version.version
        self._skills[skill.id] = skill
        log.info("skill_captured", name=name, steps=len(steps), risk=str(version.risk))
        return skill

    def add_version(self, skill_id: UUID, version: SkillVersion) -> SkillVersion:
        skill = self._require(skill_id)
        added = skill.add_version(version)
        # A new version is not live until it is tested and (if risky) approved.
        skill.status = SkillStatus.DRAFT
        return added

    # ------------------------------------------------------------------ lifecycle

    async def test(self, skill_id: UUID, *, version: int | None = None) -> SandboxResult:
        """Run the version's cases in a sandbox (§52).

        With no executor wired, this validates structure only and says so — it never
        reports a pass it did not observe.
        """
        skill = self._require(skill_id)
        target = skill.version(version) or skill.latest
        if target is None:
            raise ThursdayError("the skill has no versions", skill=skill.name)

        failures: list[str] = []
        if self._tools is not None:
            for tool in target.tools:
                if not self._tools.has(canonical(tool)):  # type: ignore[attr-defined]
                    failures.append(f"tool {tool!r} is not registered")
        if not target.steps:
            failures.append("the skill has no steps")
        for step in target.steps:
            if step.risky and step.on_error == "continue":
                failures.append(f"step {step.seq} is risky and must not continue on error")

        passed = 0
        for case in target.tests:
            try:
                await self._run_case(target, case)
                passed += 1
            except Exception as exc:
                failures.append(f"{case.name}: {exc}")

        result = SandboxResult(
            ok=not failures, passed=passed, failed=len(failures), failures=failures
        )
        if result.ok:
            skill.status = SkillStatus.TESTING
        log.info("skill_tested", name=skill.name, result=result.summary())
        return result

    def approve(
        self, skill_id: UUID, *, approved_by: str, version: int | None = None
    ) -> SkillVersion:
        skill = self._require(skill_id)
        target = skill.version(version) or skill.latest
        if target is None:
            raise ThursdayError("the skill has no versions", skill=skill.name)
        target.approved_by = approved_by
        target.approved_at = datetime.now(UTC)
        return target

    def activate(self, skill_id: UUID, *, version: int | None = None) -> Skill:
        skill = self._require(skill_id)
        target = skill.version(version) or skill.latest
        if target is None:
            raise ThursdayError("the skill has no versions", skill=skill.name)
        if skill.status is SkillStatus.DRAFT:
            raise ThursdayError(
                "a skill must pass its sandbox tests before activation", skill=skill.name
            )
        if target.needs_approval and not target.approved_by:
            # §96: a new skill may not perform destructive actions without a review.
            raise PermissionDenied(
                "this skill contains destructive or outward-facing steps and needs approval",
                skill=skill.name,
                steps=[s.tool for s in target.risky_steps],
            )
        skill.status = SkillStatus.ACTIVE
        skill.current_version = target.version
        log.info("skill_activated", name=skill.name, version=target.version)
        return skill

    def rollback(self, skill_id: UUID, *, to: int) -> Skill:
        """§53 — versions are kept, so a regression is one call away from being undone."""
        skill = self._require(skill_id)
        if skill.version(to) is None:
            raise ThursdayError(f"the skill has no version {to}", skill=skill.name)
        skill.current_version = to
        log.info("skill_rolled_back", name=skill.name, version=to)
        return skill

    def deprecate(self, skill_id: UUID) -> Skill:
        skill = self._require(skill_id)
        skill.status = SkillStatus.DEPRECATED
        return skill

    # ------------------------------------------------------------------ lookup

    def get(self, skill_id: UUID) -> Skill:
        return self._require(skill_id)

    def find(self, name_or_slug: str) -> Skill | None:
        needle = _slugify(name_or_slug)
        return next((s for s in self._skills.values() if s.slug == needle), None)

    def list(self, *, status: SkillStatus | None = None) -> list[Skill]:
        return [s for s in self._skills.values() if status is None or s.status is status]

    def active(self) -> list[Skill]:
        return self.list(status=SkillStatus.ACTIVE)

    async def _run_case(self, version: SkillVersion, case: SkillTest) -> None:
        if self._executor is None:
            raise RuntimeError("no sandbox executor is configured; structural checks only")
        for step in version.steps:
            args = {**step.args, **case.inputs}
            await self._executor.execute(  # type: ignore[attr-defined]
                ToolCall(tool=canonical(step.tool), args=args, reason=f"sandbox: {case.name}"),
                permissions=version.permissions,
                wait_for_approval=False,
            )

    def _require(self, skill_id: UUID) -> Skill:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise ThursdayError("unknown skill", skill_id=str(skill_id))
        return skill


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9฀-๿]+", "-", name.lower()).strip("-")
