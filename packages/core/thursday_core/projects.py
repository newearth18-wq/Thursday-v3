"""Projects and the Project Brain (PART 44).

A project is the unit Thursday reasons about above a task: it has a goal, a state, open
work, decisions taken, files that matter, and people involved. The Project Brain is the
assembled answer to "where is this, and what is it stuck on" — the question an owner
actually asks, which no single table can answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from thursday_shared.enums import MemoryLayer, TaskState
from thursday_shared.errors import ThursdayError
from thursday_shared.ids import new_id
from thursday_shared.models import MemoryQuery, ProjectSummary, utcnow


@dataclass
class Decision:
    """PART 44's recent decisions — the journal entry, kept with the project."""

    id: UUID = field(default_factory=new_id)
    decision: str = ""
    reason: str = ""
    alternatives: list[str] = field(default_factory=list)
    source: str = ""
    impact: str = ""
    decided_at: datetime = field(default_factory=utcnow)


@dataclass
class Project:
    id: UUID = field(default_factory=new_id)
    name: str = ""
    description: str = ""
    goal: str = ""
    status: str = "active"
    people: list[str] = field(default_factory=list)
    important_files: list[str] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def summary(self, *, blocked_on: list[str] | None = None) -> ProjectSummary:
        return ProjectSummary(
            id=self.id,
            name=self.name,
            goal=self.goal or None,
            status=self.status,
            blocked_on=blocked_on or [],
        )


class ProjectManager:
    def __init__(self, *, tasks: object | None = None, memory: object | None = None) -> None:
        self._projects: dict[UUID, Project] = {}
        self._tasks = tasks
        self._memory = memory

    def create(self, *, name: str, goal: str = "", description: str = "", **extra: Any) -> Project:
        project = Project(name=name, goal=goal, description=description, metadata=extra)
        self._projects[project.id] = project
        return project

    def get(self, project_id: UUID) -> Project:
        project = self._projects.get(project_id)
        if project is None:
            raise ThursdayError("unknown project", project_id=str(project_id))
        return project

    def find(self, name: str) -> Project | None:
        lowered = name.strip().lower()
        return next((p for p in self._projects.values() if p.name.lower() == lowered), None)

    def list(self, *, status: str | None = None) -> list[Project]:
        rows = list(self._projects.values())
        if status is not None:
            rows = [p for p in rows if p.status == status]
        return sorted(rows, key=lambda p: p.updated_at, reverse=True)

    def record_decision(self, project_id: UUID, decision: Decision) -> Decision:
        project = self.get(project_id)
        project.decisions.append(decision)
        project.updated_at = utcnow()
        return decision

    async def brain(self, project_id: UUID) -> dict[str, Any]:
        """PART 44. Everything Thursday knows about a project, assembled.

        Answers "what is this blocked on" from the task table rather than from a status
        field someone forgot to update — a blocked task *is* the blockage.
        """
        project = self.get(project_id)
        open_tasks: list[Any] = []
        blocked_on: list[str] = []

        if self._tasks is not None:
            for task in self._tasks.list(project_id=project_id, limit=200):  # type: ignore[attr-defined]
                if task.status.is_terminal:
                    continue
                open_tasks.append(task)
                if task.status in (TaskState.BLOCKED, TaskState.WAITING_APPROVAL, TaskState.FAILED):
                    blocked_on.append(
                        f"{task.title} ({task.status.value.lower().replace('_', ' ')})"
                    )

        memories: list[Any] = []
        if self._memory is not None:
            memories = await self._memory.recall(  # type: ignore[attr-defined]
                MemoryQuery(
                    text=project.goal or project.name,
                    project_id=project_id,
                    layers=[MemoryLayer.PROJECT, MemoryLayer.EPISODIC, MemoryLayer.PROCEDURAL],
                    k=8,
                )
            )

        return {
            "id": str(project.id),
            "name": project.name,
            "goal": project.goal,
            "status": project.status,
            "summary": project.description,
            "current_state": _describe_state(open_tasks, blocked_on),
            "open_tasks": [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "status": t.status.value,
                    "progress": t.progress,
                }
                for t in open_tasks[:20]
            ],
            "blocked_on": blocked_on,
            "recent_decisions": [
                {
                    "decision": d.decision,
                    "reason": d.reason,
                    "impact": d.impact,
                    "decided_at": d.decided_at.isoformat(),
                }
                for d in project.decisions[-5:]
            ],
            "important_files": project.important_files,
            "people": project.people,
            "timeline": [
                {"at": d.decided_at.isoformat(), "what": d.decision}
                for d in project.decisions[-10:]
            ],
            "relevant_memories": [
                {"content": m.content, "layer": str(m.layer), "confidence": m.confidence}
                for m in memories
            ],
        }


def _describe_state(open_tasks: list[Any], blocked_on: list[str]) -> str:
    if blocked_on:
        return f"blocked on {len(blocked_on)} item(s): {'; '.join(blocked_on[:3])}"
    if open_tasks:
        return f"{len(open_tasks)} task(s) in progress"
    return "nothing open"
