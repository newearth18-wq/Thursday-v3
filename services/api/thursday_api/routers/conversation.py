"""POST /conversation — the one endpoint the user's own words go through."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from thursday_core.container import Container
from thursday_shared.ids import new_id
from thursday_shared.models import UserRequest

from thursday_api.deps import get_container
from thursday_api.schemas import ConversationRequest, ConversationResponse

router = APIRouter(prefix="/conversations", tags=["conversation"])


@router.post("", response_model=ConversationResponse)
async def talk(
    request: ConversationRequest, c: Container = Depends(get_container)
) -> ConversationResponse:
    """PART 6/7. Everything the owner says arrives here, in every modality."""
    session_id = request.session_id or new_id()
    response = await c.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text=request.text,
            device_id=request.device_id,
            modality=request.modality,
            screen_context=request.screen,
            selection_context=request.selection,
            wait_for_approval=request.wait_for_approval,
        )
    )
    return ConversationResponse(
        session_id=session_id,
        text=response.text,
        voice_mode=response.voice_mode.value,
        avatar_state=response.avatar_state,
        confidence=response.confidence,
        verified=response.verified,
        detail=response.detail,
        task_id=response.task_id,
        status=response.status.value if response.status else None,
        intent=response.intent.model_dump(mode="json") if response.intent else None,
        citations=[c_.model_dump(mode="json") for c_ in response.citations],
        approvals=[a.model_dump(mode="json") for a in response.approvals],
        actions=response.actions,
        ui_events=[e.model_dump(mode="json") for e in response.ui_events],
        speech=response.speech.model_dump(mode="json") if response.speech else None,
        trace_id=response.trace_id,
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
