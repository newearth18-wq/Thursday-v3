"""WS /realtime — the client channel (§11.9).

Clients send turns, context updates and approvals; the server streams tokens, state, task
and agent updates, approval requests and notifications. Every server-side event the client
cares about arrives here rather than by polling.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from thursday.core.logging import get_logger
from thursday.shared.enums import ApprovalScope
from thursday.shared.ids import new_id
from thursday.shared.models import Event, ScreenContext

log = get_logger(__name__)
router = APIRouter()

#: Event kinds pushed to connected clients.
CLIENT_EVENTS = (
    "task.", "agent.", "approval.", "device.", "memory.conflict", "system.",
)


@router.websocket("/realtime")
async def realtime(websocket: WebSocket) -> None:
    container = websocket.app.state.container
    await websocket.accept()
    session_id: UUID = new_id()
    outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

    async def forward(event: Event) -> None:
        if not any(event.kind.startswith(prefix) for prefix in CLIENT_EVENTS):
            return
        try:
            outbox.put_nowait(
                {"type": "event", "kind": event.kind, "payload": event.payload,
                 "task_id": str(event.task_id) if event.task_id else None}
            )
        except asyncio.QueueFull:
            # A slow client must not stall the core; it will resync from /world.
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
                reply = await container.engine.handle_turn(
                    session_id=UUID(message.get("session_id", str(session_id))),
                    text=message.get("text", ""),
                    device_id=UUID(message["device_id"]) if message.get("device_id") else None,
                    modality=message.get("modality", "text"),
                    screen=ScreenContext.model_validate(message["screen"]) if message.get("screen") else None,
                )
                await outbox.put({
                    "type": "reply",
                    "text": reply.text,
                    "voice_mode": reply.voice_mode.value,
                    "avatar_state": reply.avatar_state,
                    "verified": reply.verified,
                    "confidence": reply.confidence,
                    "approvals": [a.model_dump(mode="json") for a in reply.approvals],
                })
            elif kind == "interrupt":
                reply = await container.engine.handle_turn(session_id=session_id, text="stop")
                await outbox.put({"type": "reply", "text": reply.text, "voice_mode": "NORMAL"})
            elif kind == "approve":
                await container.approvals.decide(
                    UUID(message["approval_id"]),
                    approve=bool(message.get("approve", True)),
                    scope=ApprovalScope(message.get("scope", "once")),
                )
            elif kind == "context_update":
                container.world.update(
                    active_app=message.get("active_app"),
                    active_device_name=message.get("device_name") or container.world.snapshot().active_device_name,
                )
            elif kind == "ping":
                await outbox.put({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("realtime_error", error=str(exc))
    finally:
        pump_task.cancel()
