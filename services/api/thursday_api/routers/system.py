"""System endpoints: health, world state, audit, undo, emergency stop."""

from __future__ import annotations

from enum import IntEnum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_security.policy import HARD_BLOCKED
from thursday_shared.enums import AutonomyLevel, PolicyDecision, ProactivityLevel
from thursday_shared.errors import ThursdayError

from thursday_api.deps import get_container
from thursday_api.schemas import EmergencyStopRequest

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(c: Container = Depends(get_container)) -> dict:
    checks = await c.health()
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def _subset(checks: list[dict], prefix: str) -> dict:
    """PART 91's per-component endpoints, so a probe can target one dependency."""
    matching = [check for check in checks if check["component"].startswith(prefix)]
    return {
        "ok": bool(matching) and all(check["ok"] for check in matching),
        "checks": matching,
    }


@router.get("/health/database")
async def health_database(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "database")


@router.get("/health/redis")
async def health_redis(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "redis")


@router.get("/health/devices")
async def health_devices(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "devices")


@router.get("/health/models")
async def health_models(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "model")


@router.get("/world-state")
async def world_state(c: Container = Depends(get_container)) -> dict:
    """PART 45. The 'now' Thursday reasons against."""
    return c.world.snapshot().model_dump(mode="json")


@router.get("/world")
async def world(c: Container = Depends(get_container)) -> dict:
    return c.world.snapshot().model_dump(mode="json")


@router.get("/autonomy")
async def get_autonomy(c: Container = Depends(get_container)) -> dict:
    """PART 97. Two dials, deliberately separate: one for acting, one for speaking."""
    return {
        "autonomy": c.permissions.autonomy.name,
        "autonomy_level": int(c.permissions.autonomy),
        "proactivity": c.automations.gate.level.name,
        "proactivity_level": int(c.automations.gate.level),
        "note": (
            "raising autonomy relaxes ASK_ONCE actions only; ASK_ALWAYS and BLOCK are "
            "unaffected at every level"
        ),
    }


def _level[T: IntEnum](enum: type[T], value: str) -> T:
    """Accept the name this API prints as well as the number behind it.

    ``GET /autonomy`` reports ``"MODERATE"``. Requiring ``2`` on the way back in would mean
    the value you are handed is not a value you can send.
    """
    try:
        return enum[value.upper()] if not value.lstrip("-").isdigit() else enum(int(value))
    except (KeyError, ValueError) as exc:
        allowed = ", ".join(member.name for member in enum)
        raise HTTPException(
            status_code=400, detail=f"unknown level {value!r}; try {allowed}"
        ) from exc


@router.post("/autonomy")
async def set_autonomy(
    autonomy: str | None = None,
    proactivity: str | None = None,
    c: Container = Depends(get_container),
) -> dict:
    if autonomy is not None:
        c.permissions.set_autonomy(_level(AutonomyLevel, autonomy))
    if proactivity is not None:
        c.automations.gate.level = _level(ProactivityLevel, proactivity)
    return await get_autonomy(c)


@router.get("/agents")
async def agents(c: Container = Depends(get_container)) -> dict:
    return {
        "agents": [
            {
                **spec.model_dump(mode="json"),
                "success_rate": round(c.agents.success_rate(spec.name), 3),
            }
            for spec in c.agents.specs()
        ]
    }


@router.get("/tools")
async def tools(c: Container = Depends(get_container)) -> dict:
    return {"tools": [spec.model_dump(mode="json") for spec in c.tools.specs()]}


@router.get("/policies")
async def policies(c: Container = Depends(get_container)) -> dict:
    """PART 70. Every action Thursday knows, and what it will do when asked to take it.

    The decision reported here is the *effective* one: the table's default with the current
    autonomy level already applied, so what the panel shows is what will actually happen.
    """
    autonomy = c.permissions.autonomy
    table = c.permissions.policy
    rows = []
    for action in table.known_actions():
        policy = table.get(action, autonomy=autonomy)
        rows.append(
            {
                "action": action,
                "namespace": action.split(".")[0],
                "decision": policy.default.value,
                "level": policy.level.name,
                "risk": policy.risk.value,
                "reversible": policy.reversible,
                "requires_backup": policy.requires_backup,
                "bulk_threshold": policy.bulk_threshold,
                "blocked": table.is_blocked(action),
                # An ASK_ALWAYS action can be tightened but never loosened (ADR 0008), so the
                # panel greys out the control instead of offering a choice that would not stick.
                "can_relax": table.can_relax(action),
            }
        )
    return {"autonomy": autonomy.name, "policies": rows, "hard_blocked": sorted(HARD_BLOCKED)}


@router.post("/policies/{action}")
async def set_policy(
    action: str, decision: PolicyDecision, c: Container = Depends(get_container)
) -> dict:
    """Change one action's approval mode.

    A request the table would silently ignore is refused instead. A setting that appears to
    save and then does nothing is worse than an error: the owner would believe deleting files
    now happens without asking, and it would not.
    """
    table = c.permissions.policy
    try:
        table.override(action, decision)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective = table.get(action, autonomy=c.permissions.autonomy)
    if effective.default is not decision:
        table.clear_override(action)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{action!r} cannot be set to {decision.value}: it stays "
                f"{effective.default.value} because it is a system-level or "
                "ask-every-time action"
            ),
        )
    return {"action": action, "decision": effective.default.value}


@router.get("/audit")
async def audit(
    task_id: UUID | None = None,
    tool: str | None = None,
    limit: int = 100,
    c: Container = Depends(get_container),
) -> dict:
    entries = c.audit.entries(task_id=task_id, tool=tool, limit=limit)
    return {
        "entries": [e.model_dump(mode="json") for e in entries],
        "chain_intact": c.audit.verify_chain(),
    }


@router.get("/undo")
async def undo_list(c: Container = Depends(get_container)) -> dict:
    return {"undoable": [u.model_dump(mode="json") for u in c.undo.pending()]}


@router.post("/undo/{action_id}")
async def undo(action_id: UUID, c: Container = Depends(get_container)) -> dict:
    try:
        return {"undone": await c.undo.undo(action_id)}
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@router.post("/emergency/stop")
async def emergency_stop(
    request: EmergencyStopRequest, c: Container = Depends(get_container)
) -> dict:
    """§69. A plain endpoint on purpose — it must work when the model is down."""
    return {"scope": request.scope, "actions": await c.emergency_stop(request.scope)}


@router.post("/emergency/release")
async def release_lockdown(c: Container = Depends(get_container)) -> dict:
    c.permissions.set_lockdown(False)
    return {"lockdown": False}
