"""System endpoints: health, world state, audit, undo, emergency stop."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_shared.enums import AutonomyLevel, ProactivityLevel
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


@router.post("/autonomy")
async def set_autonomy(
    autonomy: AutonomyLevel | None = None,
    proactivity: ProactivityLevel | None = None,
    c: Container = Depends(get_container),
) -> dict:
    if autonomy is not None:
        c.permissions.set_autonomy(autonomy)
    if proactivity is not None:
        c.automations.gate.level = proactivity
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
