"""System endpoints: health, world state, audit, undo, emergency stop."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_shared.errors import ThursdayError

from thursday_api.deps import get_container
from thursday_api.schemas import EmergencyStopRequest

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(c: Container = Depends(get_container)) -> dict:
    checks = await c.health()
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


@router.get("/world")
async def world(c: Container = Depends(get_container)) -> dict:
    return c.world.snapshot().model_dump(mode="json")


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
