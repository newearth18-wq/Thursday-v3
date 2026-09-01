"""WS /realtime — the client channel (PART 72).

Clients send turns, context updates and approvals; the server streams tokens, state, task
and agent updates, approval requests and notifications. Every server-side event the client
cares about arrives here rather than by polling.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from thursday_core.logging import get_logger
from thursday_shared.enums import ApprovalScope
from thursday_shared.ids import new_id
from thursday_shared.models import Event, ScreenContext, UserRequest

log = get_logger(__name__)
router = APIRouter()

#: PART 72's client vocabulary. Internal event kinds are translated on the way out, so the
#: UI codes against a small stable set rather than against the core's internal topics.
EVENT_TRANSLATION: dict[str, str] = {
    "task.": "task.updated",
    "agent.": "agent.updated",
    "approval.required": "approval.required",
    "approval.": "approval.resolved",
    "device.": "device.updated",
    "memory.conflict": "notification",
    "memory.confirmation_required": "notification",
    "notification.": "notification",
    "automation.": "notification",
    "system.": "notification",
}


def client_event(kind: str) -> str | None:
    """Translate an internal event kind into the client vocabulary, or None to drop it."""
    for prefix, translated in EVENT_TRANSLATION.items():
        if kind.startswith(prefix):
            return translated
    return None


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    container = websocket.app.state.container
    await websocket.accept()
    session_id: UUID = new_id()
    outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

    async def forward(event: Event) -> None:
        translated = client_event(event.kind)
        if translated is None:
            return
        try:
            outbox.put_nowait(
                {
                    "type": translated,
                    "kind": event.kind,
                    "payload": event.payload,
                    "task_id": str(event.task_id) if event.task_id else None,
                    "priority": event.priority.value,
                }
            )
        except asyncio.QueueFull:
            # A slow client must not stall the core; it resyncs from /world-state.
            log.warning("realtime_backpressure", session_id=str(session_id))

    container.bus.subscribe("*", forward)

    async def pump() -> None:
        while True:
            message = await outbox.get()
            await websocket.send_text(json.dumps(message, ensure_ascii=False, default=str))

    pump_task = asyncio.create_task(pump())
    try:
        await websocket.send_text(json.dumps({"type": "ready", "session_id": str(session_id)}))
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            kind = message.get("type")

            if kind == "turn":
                response = await container.engine.handle_request(
                    UserRequest(
                        conversation_id=UUID(message.get("session_id", str(session_id))),
                        text=message.get("text", ""),
                        device_id=UUID(message["device_id"]) if message.get("device_id") else None,
                        modality=message.get("modality", "text"),
                        screen_context=(
                            ScreenContext.model_validate(message["screen"])
                            if message.get("screen")
                            else None
                        ),
                    )
                )
                # PART 72: `assistant.delta` is the streaming text channel. The reply is
                # emitted as a single delta plus a completion, so a client written for
                # streaming works unchanged once token streaming lands.
                await outbox.put(
                    {
                        "type": "assistant.delta",
                        "text": response.text,
                        "final": True,
                        "voice_mode": response.voice_mode.value,
                        "avatar_state": response.avatar_state,
                        "verified": response.verified,
                        "confidence": response.confidence,
                        "task_id": str(response.task_id) if response.task_id else None,
                        "approvals": [a.model_dump(mode="json") for a in response.approvals],
                        "ui_events": [e.model_dump(mode="json") for e in response.ui_events],
                    }
                )
                if response.speech is not None:
                    # The directive, not the waveform: audio is synthesised at the edge, so
                    # the socket never carries megabytes the client may not even play.
                    await outbox.put(
                        {"type": "assistant.audio", **response.speech.model_dump(mode="json")}
                    )
            elif kind == "interrupt":
                # PART 98 — highest priority, and it bypasses the planner.
                reply = await container.engine.handle_turn(session_id=session_id, text="stop")
                await outbox.put(
                    {
                        "type": "assistant.delta",
                        "text": reply.text,
                        "final": True,
                        "voice_mode": "NORMAL",
                    }
                )
            elif kind == "approve":
                await container.approvals.decide(
                    UUID(message["approval_id"]),
                    approve=bool(message.get("approve", True)),
                    scope=ApprovalScope(message.get("scope", "once")),
                )
            elif kind == "context_update":
                container.world.update(
                    active_app=message.get("active_app"),
                    active_device_name=message.get("device_name")
                    or container.world.snapshot().active_device_name,
                )
            elif kind == "ping":
                await outbox.put({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("realtime_error", error=str(exc))
    finally:
        pump_task.cancel()
