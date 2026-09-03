"""Projects, the Project Brain, and the decision journal (PART 44, 55, 71)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_core.projects import Decision
from thursday_shared.errors import ThursdayError

from thursday_api.deps import get_container
from thursday_api.schemas import DecisionRequest, ProjectCreateRequest

router = APIRouter(tags=["projects"])


@router.get("/projects")
async def list_projects(status: str | None = None, c: Container = Depends(get_container)) -> dict:
    return {
        "projects": [
            {
                "id": str(p.id),
                "name": p.name,
                "goal": p.goal,
                "status": p.status,
                "decisions": len(p.decisions),
                "updated_at": p.updated_at.isoformat(),
            }
            for p in c.projects.list(status=status)
        ]
    }


@router.post("/projects")
async def create_project(
    request: ProjectCreateRequest, c: Container = Depends(get_container)
) -> dict:
    project = c.projects.create(
        name=request.name, goal=request.goal, description=request.description
    )
    return {"id": str(project.id), "name": project.name, "status": project.status}


@router.get("/projects/{project_id}")
async def get_project(project_id: UUID, c: Container = Depends(get_container)) -> dict:
    try:
        project = c.projects.get(project_id)
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc
    return {
        "id": str(project.id),
        "name": project.name,
        "goal": project.goal,
        "description": project.description,
        "status": project.status,
        "people": project.people,
        "important_files": project.important_files,
    }


@router.get("/projects/{project_id}/brain")
async def project_brain(project_id: UUID, c: Container = Depends(get_container)) -> dict:
    """PART 44. Assembled, not stored: "what is this blocked on" is answered from the
    task table, so it cannot go stale the way a status field does."""
    try:
        return await c.projects.brain(project_id)
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@router.post("/decisions")
async def record_decision(request: DecisionRequest, c: Container = Depends(get_container)) -> dict:
    """PART 55. A decision is recorded with its reasoning and the alternatives considered —
    the parts that are impossible to reconstruct six months later."""
    decision = Decision(
        decision=request.decision,
        reason=request.reason,
        alternatives=request.alternatives,
        source=request.source,
        impact=request.impact,
    )
    if request.project_id is not None:
        try:
            c.projects.record_decision(request.project_id, decision)
        except ThursdayError as exc:
            raise HTTPException(status_code=404, detail=exc.to_dict()) from exc

    # The vault copy is what a person reads; the row is what Thursday queries.
    path = c.obsidian.decision_log(
        decision=request.decision,
        reason=request.reason,
        alternatives=request.alternatives,
        source=request.source or "conversation",
        impact=request.impact,
        project=c.projects.get(request.project_id).name if request.project_id else None,
    )
    return {"id": str(decision.id), "vault_path": str(path) if path else None}
