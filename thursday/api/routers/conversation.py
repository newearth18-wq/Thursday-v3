"""POST /conversation — the one endpoint the user's own words go through."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from thursday.api.deps import get_container
from thursday.api.schemas import ConversationRequest, ConversationResponse
from thursday.core.container import Container
from thursday.shared.ids import new_id

router = APIRouter(prefix="/conversation", tags=["conversation"])


@router.post("", response_model=ConversationResponse)
async def talk(request: ConversationRequest, c: Container = Depends(get_container)) -> ConversationResponse:
    session_id = request.session_id or new_id()
    reply = await c.engine.handle_turn(
        session_id=session_id,
        text=request.text,
        device_id=request.device_id,
        modality=request.modality,
        screen=request.screen,
        selection=request.selection,
        wait_for_approval=request.wait_for_approval,
    )
    return ConversationResponse(
        session_id=session_id,
        text=reply.text,
        voice_mode=reply.voice_mode.value,
        avatar_state=reply.avatar_state,
        confidence=reply.confidence,
        verified=reply.verified,
        detail=reply.detail,
        intent=reply.intent.model_dump(mode="json") if reply.intent else None,
        citations=[c_.model_dump(mode="json") for c_ in reply.citations],
        approvals=[a.model_dump(mode="json") for a in reply.approvals],
        trace_id=reply.trace_id,
    )


@router.post("/{session_id}/interrupt")
async def interrupt(session_id: UUID, c: Container = Depends(get_container)) -> dict:
    """§44 — 'Thursday, stop'. Reachable without the reasoning engine."""
    reply = await c.engine.handle_turn(session_id=session_id, text="stop")
    return {"text": reply.text, "session_id": str(session_id)}


@router.get("/{session_id}")
async def history(session_id: UUID, limit: int = 20, c: Container = Depends(get_container)) -> dict:
    turns = c.context_engine.history(session_id, limit=limit)
    if not turns:
        raise HTTPException(status_code=404, detail="no conversation with that id")
    return {"session_id": str(session_id), "turns": [t.model_dump(mode="json") for t in turns]}
