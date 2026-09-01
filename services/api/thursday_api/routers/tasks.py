"""Task endpoints (§41–43)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_shared.enums import TaskState
from thursday_shared.errors import ThursdayError
from thursday_shared.models import Budget

from thursday_api.deps import get_container
from thursday_api.schemas import TaskCreateRequest

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("")
async def create(request: TaskCreateRequest, c: Container = Depends(get_container)) -> dict:
    task = await c.tasks.create(
        title=request.title or request.objective[:80],
        objective=request.objective,
        project_id=request.project_id,
        origin_device_id=request.device_id,
        priority=request.priority,
        budget=Budget(
            usd=request.budget_usd or c.settings.default_task_budget_usd,
            seconds=c.settings.default_task_budget_seconds,
        ),
    )
    return task.model_dump(mode="json")


@router.get("")
async def list_tasks(
    status: TaskState | None = None,
    project_id: UUID | None = None,
    limit: int = 50,
    c: Container = Depends(get_container),
) -> dict:
    tasks = c.tasks.list(status=status, project_id=project_id, limit=limit)
    return {"tasks": [t.model_dump(mode="json") for t in tasks], "count": len(tasks)}


@router.get("/{task_id}")
async def get_task(task_id: UUID, c: Container = Depends(get_container)) -> dict:
    task = c.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return task.model_dump(mode="json")


@router.post("/{task_id}/pause")
async def pause(task_id: UUID, c: Container = Depends(get_container)) -> dict:
    """PART 5 — a paused task keeps its plan; it resumes where it stopped."""
    if c.tasks.get(task_id) is None:
        raise HTTPException(status_code=404, detail="unknown task")
    try:
        task = await c.tasks.pause(task_id)
    except ThursdayError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    return {"status": task.status.value}


@router.post("/{task_id}/resume")
async def resume(task_id: UUID, c: Container = Depends(get_container)) -> dict:
    if c.tasks.get(task_id) is None:
        raise HTTPException(status_code=404, detail="unknown task")
    try:
        task = await c.tasks.resume(task_id)
    except ThursdayError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    return {"status": task.status.value}


@router.post("/{task_id}/cancel")
async def cancel(task_id: UUID, c: Container = Depends(get_container)) -> dict:
    task = c.tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    if task.status.is_terminal:
        return {"status": task.status.value, "note": "already finished"}
    c.queue.cancel(task_id)
    task = await c.tasks.cancel(task_id)
    return {"status": task.status.value}
