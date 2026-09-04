"""WS /realtime — the client channel (PART 72).

Clients send turns, context updates and approvals; the server streams tokens, state, task
and agent updates, approval requests and notifications. Every server-side event the client
cares about arrives here rather than by polling.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from thursday_core.expression import Turn, express
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


#: How often the socket re-reads the parts of Thursday that do not announce themselves.
#: A database that has died emits no event, so a purely event-driven refresh would leave a
#: calm face on a broken machine until the owner happened to type something.
HEALTH_EVERY_SECONDS = 20.0


class ExpressionFeed:
    """One client's live view of what Thursday is doing and how it is going (Sprint 80).

    Two inputs need an `await` and the rest do not, so they are treated differently: world
    state is read for free on every event, and health is re-read on a timer. Both go into
    the same `express()` call, because the alternative — a cheap mood on the socket and an
    honest one on the endpoint — is two sources of truth for one feeling, and they would
    disagree exactly when it mattered.
    """

    def __init__(self, container: Any) -> None:
        self._container = container
        self._unhealthy = 0
        self._last: dict[str, object] | None = None
        self.turn = Turn()

    async def refresh_health(self) -> None:
        try:
            checks = await self._container.health()
        except Exception as exc:  # a health check that raises is itself a health problem
            log.warning("expression_health_failed", error=str(exc))
            self._unhealthy = max(self._unhealthy, 1)
            return
        self._unhealthy = sum(1 for check in checks if not check["ok"])

    def payload(self) -> dict[str, object]:
        return express(
            self._container.world.snapshot(),
            unhealthy=self._unhealthy,
            lockdown=bool(self._container.permissions.lockdown),
            turn=self.turn,
        ).payload()

    def changed(self) -> dict[str, object] | None:
        """The message to send, or None when nothing about Thursday has changed.

        Sending only on change is what keeps the animation still while Thursday is still.
        """
        current = self.payload()
        if current == self._last:
            return None
        self._last = current
        return {"type": "expression", **current}


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

    feed = ExpressionFeed(container)

    async def send(message: dict) -> None:
        await websocket.send_text(json.dumps(message, ensure_ascii=False, default=str))

    async def announce() -> None:
        """Queue an expression update if there is one.

        Through the outbox rather than straight down the socket: the receive loop and the
        pump both want to write, and one queue means they never interleave a frame.
        """
        if (update := feed.changed()) is not None:
            await outbox.put(update)

    async def pump() -> None:
        """Drain the outbox, and wake on a timer even when nothing arrives.

        The timeout is the whole reason this is not a plain `await outbox.get()`: it is the
        only moment a quiet, broken Thursday gets to say so.
        """
        while True:
            try:
                message = await asyncio.wait_for(outbox.get(), timeout=HEALTH_EVERY_SECONDS)
            except TimeoutError:
                await feed.refresh_health()
            else:
                await send(message)
            if (update := feed.changed()) is not None:
                await send(update)

    await send({"type": "ready", "session_id": str(session_id)})
    # The first paint, before anything has happened. A client that opened onto a broken
    # machine should not have to wait a tick to find out. Sent before the pump starts, so
    # only ever one coroutine is writing to this socket.
    await feed.refresh_health()
    if (opening := feed.changed()) is not None:
        await send(opening)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await websocket.receive_text()
            message = json.loads(raw)
            kind = message.get("type")

            if kind == "turn":
                # Said before the work starts, not after: "กำลังฟังอยู่" that arrives with
                # the answer is not a state, it is a caption.
                feed.turn = Turn(thinking=True)
                await announce()
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
                # `verified` and `confidence` come from the response rather than from
                # anything this handler decides — ADR 0012's rule is that no caller can
                # assert success, and that includes asserting a confident face.
                feed.turn = Turn(
                    speaking=response.speech is not None,
                    verified=response.verified,
                    confidence=response.confidence,
                )
                await announce()
            elif kind == "interrupt":
                # PART 98 — highest priority, and it bypasses the planner.
                feed.turn = Turn()
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
