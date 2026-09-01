"""Approval endpoints (§38)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from thursday.api.deps import get_container
from thursday.api.schemas import ApprovalDecisionRequest
from thursday.core.container import Container
from thursday.shared.enums import ApprovalScope

router = APIRouter(prefix="/approvals", tags=["approvals"])

_SCOPES = {
    "approve": ApprovalScope.ONCE,
    "approve_once": ApprovalScope.ONCE,
    "always_allow": ApprovalScope.ALWAYS,
}


@router.get("")
async def pending(c: Container = Depends(get_container)) -> dict:
    rows = c.approvals.pending()
    return {"approvals": [a.model_dump(mode="json") for a in rows], "count": len(rows)}


@router.post("/{approval_id}")
async def decide(
    approval_id: UUID, request: ApprovalDecisionRequest, c: Container = Depends(get_container)
) -> dict:
    if c.approvals.get(approval_id) is None:
        raise HTTPException(status_code=404, detail="unknown approval")
    if request.decision not in {*_SCOPES, "reject"}:
        raise HTTPException(
            status_code=400, detail="decision must be approve, approve_once, always_allow or reject"
        )

    approve = request.decision != "reject"
    scope = request.scope or _SCOPES.get(request.decision, ApprovalScope.ONCE)
    approval = await c.approvals.decide(
        approval_id, approve=approve, scope=scope, note=request.note
    )
    return approval.model_dump(mode="json")


@router.get("/grants")
async def grants(c: Container = Depends(get_container)) -> dict:
    """Every standing grant, so nothing accumulates unseen (§8.4, T5)."""
    return {"grants": [g.model_dump(mode="json") for g in c.permissions.list_grants()]}


@router.delete("/grants/{grant_id}")
async def revoke(grant_id: UUID, c: Container = Depends(get_container)) -> dict:
    return {"revoked": c.permissions.revoke_grant(grant_id)}
