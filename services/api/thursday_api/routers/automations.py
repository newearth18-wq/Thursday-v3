"""Automation endpoints — the workflow builder's back end (§48, V15).

`/automations` has been in the API's `EXPENSIVE_PREFIXES` list since rate limiting was
written, and there were no routes under it. The engine ran, the rules existed, and nothing
outside the process could see or change one.

Two things here are deliberate and neither is obvious from the route list.

**Nothing arrives enabled.** `POST` stores a rule with `enabled=False` whatever the body
says, and turning it on is a separate call the owner makes after reading the preview. A
builder that saved a running rule would make "save" and "arm" the same button.

**`/preview` never writes and never runs.** It validates the graph, resolves what each
action would mean to the Permission Engine, and says which handlers are actually wired —
then returns. It is the endpoint the canvas calls on every edit.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from thursday_automation.cron import CronRefused, describe
from thursday_automation.workflow import (
    ACTION_NEEDS,
    CONDITION_OPS,
    TRIGGER_RUNNERS,
    Node,
    Workflow,
    WorkflowRefused,
    explain,
    from_automation,
    preview,
)
from thursday_core.container import Container

from thursday_api.deps import get_container

router = APIRouter(prefix="/automations", tags=["automations"])


class NodeBody(BaseModel):
    id: str
    kind: str
    subkind: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0


class WorkflowBody(BaseModel):
    name: str = ""
    nodes: list[NodeBody] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    follow_up_from: int | None = None


def _workflow(body: WorkflowBody, *, automation_id: UUID | None = None) -> Workflow:
    return Workflow(
        name=body.name,
        nodes=[Node(n.id, n.kind, n.subkind, dict(n.config), n.x, n.y) for n in body.nodes],  # type: ignore[arg-type]
        order=list(body.order),
        follow_up_from=body.follow_up_from,
        automation_id=automation_id,
    )


def _wired(c: Container) -> set[str]:
    """Which action handlers the running container actually has.

    Read off the container rather than assumed, because the answer differs between a full
    install and a fresh one, and the fresh one is where a silently skipped action would do
    its damage.
    """
    return {name for name in set(ACTION_NEEDS.values()) if name and getattr(c, name, None)}


def _report(c: Container, workflow: Workflow) -> dict[str, Any]:
    problems = workflow.validate()
    consequences = preview(workflow, policies=c.policy, wired=_wired(c))
    return {
        "valid": not any(p.severity == "error" for p in problems),
        "problems": [p.to_dict() for p in problems],
        "count": len(problems),
        "items": [p.to_dict() for p in problems],
        "consequences": [con.to_dict() for con in consequences],
        "explanation": explain(workflow),
    }


@router.get("")
async def listing(c: Container = Depends(get_container)) -> dict:
    """Every rule, as a graph the canvas can open — including ones Thursday suggested."""
    rows = []
    for automation in c.automations.list():
        workflow = from_automation(automation)
        rows.append(
            {
                **workflow.to_dict(),
                "created_by": automation.created_by,
                "run_count": automation.run_count,
                "last_run_at": automation.last_run_at.isoformat()
                if automation.last_run_at
                else None,
                "explanation": explain(workflow),
            }
        )
    return {"automations": rows, "count": len(rows), "items": rows}


@router.get("/catalogue")
async def catalogue(c: Container = Depends(get_container)) -> dict:
    """What the canvas is allowed to offer, and what each piece would cost.

    Served rather than hardcoded in the client so the two cannot drift: a trigger kind with
    no runner, or a tool the Permission Engine blocks, is named here as unavailable rather
    than being a box the owner can drag on and discover later.
    """
    wired = _wired(c)
    return {
        "triggers": [
            {"kind": kind, "unavailable": reason} for kind, reason in TRIGGER_RUNNERS.items()
        ],
        "conditions": list(CONDITION_OPS),
        "actions": [
            {
                "kind": kind,
                "needs": needs,
                "unavailable": (
                    f"ยังไม่ได้ต่อ {needs} — การกระทำนี้จะถูกข้ามไปเงียบ ๆ"
                    if needs and needs not in wired
                    else ""
                ),
            }
            for kind, needs in ACTION_NEEDS.items()
        ],
        "tools": [
            {
                "name": spec.name,
                "level": c.policy.get(spec.name).level.name,
                "decision": str(c.policy.get(spec.name).default),
                "blocked": c.policy.is_blocked(spec.name),
            }
            for spec in c.tools.specs()
        ],
    }


@router.post("/preview")
async def dry_run(body: WorkflowBody, c: Container = Depends(get_container)) -> dict:
    """Validate and explain. Writes nothing, runs nothing, costs nothing."""
    return _report(c, _workflow(body))


@router.post("")
async def create(body: WorkflowBody, c: Container = Depends(get_container)) -> dict:
    workflow = _workflow(body)
    try:
        automation = workflow.to_automation()
    except WorkflowRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # Whatever the body said. Saving and arming are different decisions.
    automation.enabled = False
    c.automations.add(automation)
    return {"id": str(automation.id), "enabled": False, **_report(c, workflow)}


@router.put("/{automation_id}")
async def update(
    automation_id: UUID, body: WorkflowBody, c: Container = Depends(get_container)
) -> dict:
    existing = _find(c, automation_id)
    workflow = _workflow(body, automation_id=automation_id)
    try:
        replacement = workflow.to_automation()
    except WorkflowRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    # An edit does not arm a rule, and does not disarm one either: the owner's last
    # decision about whether this runs survives a change to what it does.
    replacement.enabled = existing.enabled
    replacement.run_count = existing.run_count
    replacement.last_run_at = existing.last_run_at
    c.automations.add(replacement)
    return {"id": str(automation_id), "enabled": replacement.enabled, **_report(c, workflow)}


@router.post("/{automation_id}/enable")
async def enable(
    automation_id: UUID, enabled: bool = True, c: Container = Depends(get_container)
) -> dict:
    _find(c, automation_id)
    automation = c.automations.enable(automation_id, enabled=enabled)
    return {"id": str(automation_id), "enabled": automation.enabled, "name": automation.name}


@router.post("/{automation_id}/run")
async def run_now(automation_id: UUID, c: Container = Depends(get_container)) -> dict:
    """Run it once, now, because the owner asked.

    Works on a disabled rule on purpose: trying it before arming it is the whole point of
    the button. Every action inside still passes the Permission Engine — running by hand
    does not grant the rule anything it would not have had on its own.
    """
    automation = _find(c, automation_id)
    results = await c.automations.run(automation)
    return {"id": str(automation_id), "ran": True, "steps": len(results), "results": results}


@router.delete("/{automation_id}")
async def remove(automation_id: UUID, c: Container = Depends(get_container)) -> dict:
    _find(c, automation_id)
    c.automations.remove(automation_id)
    return {"id": str(automation_id), "removed": True}


@router.get("/schedule/describe")
async def describe_schedule(cron: str) -> dict:
    """Read a cron expression back in plain words, or say it cannot be read.

    The canvas calls this as the owner types, because "is this what I meant" is a question
    best answered before the rule is saved rather than after it fails to run.
    """
    try:
        return {"cron": cron, "reads_as": describe(cron), "valid": True}
    except CronRefused as exc:
        return {"cron": cron, "reads_as": "", "valid": False, "problem": str(exc)}


def _find(c: Container, automation_id: UUID):
    automation = next((a for a in c.automations.list() if a.id == automation_id), None)
    if automation is None:
        raise HTTPException(status_code=404, detail="unknown automation")
    return automation
