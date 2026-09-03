"""Skills and automations (§11.7, §48–53)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from thursday_automation.skills.models import SkillStatus, SkillStep, SkillTest
from thursday_core.container import Container
from thursday_shared.errors import PermissionDenied, ThursdayError

from thursday_api.deps import get_container

router = APIRouter(tags=["skills", "automations"])


class SkillStepIn(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = None
    on_error: str = "stop"


class SkillCaptureIn(BaseModel):
    name: str
    description: str = ""
    steps: list[SkillStepIn]
    tests: list[dict[str, Any]] = Field(default_factory=list)


def _render(skill: Any) -> dict[str, Any]:
    version = skill.version()
    return {
        "id": str(skill.id),
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "status": skill.status.value,
        "current_version": skill.current_version,
        "versions": [v.version for v in skill.versions],
        "risk": version.risk.value if version else "LOW",
        "needs_approval": version.needs_approval if version else False,
        "approved_by": version.approved_by if version else None,
        "risky_steps": [s.tool for s in version.risky_steps] if version else [],
    }


@router.get("/skills")
async def list_skills(
    status: SkillStatus | None = None, c: Container = Depends(get_container)
) -> dict:
    return {"skills": [_render(s) for s in c.skills.list(status=status)]}


@router.post("/skills")
async def capture(request: SkillCaptureIn, c: Container = Depends(get_container)) -> dict:
    skill = c.skills.capture(
        name=request.name,
        description=request.description,
        steps=[
            SkillStep(seq=i, tool=s.tool, args=s.args, condition=s.condition, on_error=s.on_error)
            for i, s in enumerate(request.steps)
        ],
        tests=[SkillTest(**t) for t in request.tests],
    )
    return _render(skill)


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: UUID, c: Container = Depends(get_container)) -> dict:
    try:
        return _render(c.skills.get(skill_id))
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@router.post("/skills/{skill_id}/test")
async def test_skill(
    skill_id: UUID, version: int | None = None, c: Container = Depends(get_container)
) -> dict:
    """§52 — a skill is proven on fixtures before it touches real data."""
    try:
        result = await c.skills.test(skill_id, version=version)
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return {
        "ok": result.ok,
        "passed": result.passed,
        "failed": result.failed,
        "failures": result.failures,
        "summary": result.summary(),
    }


@router.post("/skills/{skill_id}/approve")
async def approve_skill(
    skill_id: UUID,
    approved_by: str = "owner",
    version: int | None = None,
    c: Container = Depends(get_container),
) -> dict:
    try:
        approved = c.skills.approve(skill_id, approved_by=approved_by, version=version)
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return {"version": approved.version, "approved_by": approved.approved_by}


@router.post("/skills/{skill_id}/activate")
async def activate_skill(
    skill_id: UUID, version: int | None = None, c: Container = Depends(get_container)
) -> dict:
    try:
        return _render(c.skills.activate(skill_id, version=version))
    except PermissionDenied as exc:
        # A risky skill that has not been reviewed cannot be activated by asking twice.
        raise HTTPException(status_code=403, detail=exc.to_dict()) from exc
    except ThursdayError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc


@router.post("/skills/{skill_id}/rollback")
async def rollback_skill(skill_id: UUID, to: int, c: Container = Depends(get_container)) -> dict:
    try:
        return _render(c.skills.rollback(skill_id, to=to))
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@router.get("/automations")
async def list_automations(
    enabled_only: bool = False, c: Container = Depends(get_container)
) -> dict:
    return {
        "automations": [
            {
                "id": str(a.id),
                "name": a.name,
                "enabled": a.enabled,
                "created_by": a.created_by,
                "trigger": a.trigger.kind,
                "event_kind": a.trigger.event_kind,
                "cron": a.trigger.cron,
                "run_count": a.run_count,
                "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
            }
            for a in c.automations.list(enabled_only=enabled_only)
        ]
    }


@router.post("/automations/{automation_id}/enable")
async def enable_automation(
    automation_id: UUID, enabled: bool = True, c: Container = Depends(get_container)
) -> dict:
    try:
        automation = c.automations.enable(automation_id, enabled=enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown automation") from exc
    return {"id": str(automation.id), "enabled": automation.enabled}


@router.post("/automations/{automation_id}/run")
async def run_automation(automation_id: UUID, c: Container = Depends(get_container)) -> dict:
    automations = {a.id: a for a in c.automations.list()}
    if automation_id not in automations:
        raise HTTPException(status_code=404, detail="unknown automation")
    return {"results": await c.automations.run(automations[automation_id])}


@router.get("/routines/suggestions")
async def routine_suggestions(c: Container = Depends(get_container)) -> dict:
    """§49 — Thursday proposes routines; it never creates them silently."""
    return {
        "suggestions": [
            {
                "tools": list(candidate.tools),
                "hour_band": candidate.hour_band,
                "occurrences": candidate.occurrences,
                "distinct_days": candidate.distinct_days,
                "prompt": candidate.describe("th"),
            }
            for candidate in c.routines.unproposed()
        ]
    }


@router.post("/routines/suggestions/accept")
async def accept_routine(index: int = 0, c: Container = Depends(get_container)) -> dict:
    candidates = c.routines.unproposed()
    if not (0 <= index < len(candidates)):
        raise HTTPException(status_code=404, detail="no such suggestion")
    candidate = candidates[index]
    automation = c.automations.add(candidate.to_automation())
    c.routines.mark_proposed(candidate)
    # Accepted, but still disabled: enabling is a separate, deliberate act.
    return {"id": str(automation.id), "name": automation.name, "enabled": automation.enabled}
