"""Device endpoints and the TNP/1 WebSocket (§9, §21, §22)."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from thursday_core.container import Container
from thursday_core.logging import get_logger
from thursday_devices.hub import WebSocketDeviceSession
from thursday_shared.enums import PolicyDecision
from thursday_shared.errors import ThursdayError
from thursday_shared.models import ActionRequest, DeviceAction, DeviceCapabilities
from thursday_shared.protocol import (
    ActionResultFrame,
    ErrorFrame,
    Heartbeat,
    Hello,
    Welcome,
    parse_frame,
)

from thursday_api.deps import get_container
from thursday_api.schemas import DeviceActionRequest, DeviceHeartbeat, DeviceRegistration

log = get_logger(__name__)
router = APIRouter(tags=["devices"])


@router.get("/devices")
async def list_devices(c: Container = Depends(get_container)) -> dict:
    return {"devices": [d.model_dump(mode="json") for d in c.hub.all()]}


@router.get("/devices/{device_id}")
async def get_device(device_id: UUID, c: Container = Depends(get_container)) -> dict:
    summary = c.hub.summary(device_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown device")
    return summary.model_dump(mode="json")


@router.post("/devices/register")
async def register_device(
    request: DeviceRegistration, c: Container = Depends(get_container)
) -> dict:
    """Pair a device.

    The same signature the WebSocket handshake requires — a second door into the trusted
    device set would be worth exactly as much as the weaker of the two, so there is only
    one check and both doors call it.

    Registration is not connection. The reply says where to dial for commands, and the
    device stays OFFLINE until it does: a device listed as reachable that cannot receive
    anything would be selected by the router and fail three steps into a task.
    """
    outcome = c.device_auth.verify(
        device_id=str(request.device_id),
        name=request.name,
        os=request.os,
        nonce=request.nonce,
        issued_at=request.issued_at,
        signature=request.signature,
    )
    if not outcome.ok:
        log.warning("device_registration_rejected", device=request.name, reason=outcome.reason)
        raise HTTPException(status_code=401, detail="device authentication failed")

    summary = await c.hub.enrol(
        device_id=request.device_id,
        name=request.name,
        kind=request.kind,
        os=request.os,
        capabilities=DeviceCapabilities.of(*request.capabilities),
    )
    return {
        "device": summary.model_dump(mode="json"),
        "command_channel": "/api/v1/device",
        "heartbeat_s": c.settings.device_heartbeat_s,
    }


@router.post("/devices/heartbeat")
async def device_heartbeat(request: DeviceHeartbeat, c: Container = Depends(get_container)) -> dict:
    """Keep an enrolled device marked as seen.

    Signed like registration. An unauthenticated heartbeat would let anyone hold a device
    that is actually gone in the ONLINE set, and the router would keep choosing it.
    """
    outcome = c.device_auth.verify(
        device_id=str(request.device_id),
        name=request.name,
        os=request.os,
        nonce=request.nonce,
        issued_at=request.issued_at,
        signature=request.signature,
    )
    if not outcome.ok:
        log.warning("device_heartbeat_rejected", device=request.name, reason=outcome.reason)
        raise HTTPException(status_code=401, detail="device authentication failed")

    if c.hub.summary(request.device_id) is None:
        raise HTTPException(status_code=404, detail="unknown device; register first")

    await c.hub.heartbeat(request.device_id, request.telemetry)
    summary = c.hub.summary(request.device_id)
    return {"device_id": str(request.device_id), "status": summary.status if summary else None}


@router.post("/devices/{device_id}/actions")
async def act(
    device_id: UUID, request: DeviceActionRequest, c: Container = Depends(get_container)
) -> dict:
    """Direct device control. Still goes through the Permission Engine — there is no
    back door around it, for any caller."""
    summary = c.hub.summary(device_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown device")

    from thursday_devices import actions as catalogue

    spec = catalogue.get(request.action)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"unknown action {request.action!r}")

    verdict = c.permissions.decide(
        ActionRequest(
            action=request.action,
            resource=str(
                request.args.get("path")
                or request.args.get("app")
                or request.args.get("name")
                or request.args.get("url")
                or ""
            ),
            device_id=device_id,
            level=spec.level,
            risk=spec.risk,
            reversible=spec.reversible,
            expected_outcome=request.reason,
        )
    )
    if verdict.decision is not PolicyDecision.AUTO:
        raise HTTPException(
            status_code=403,
            detail={
                "decision": verdict.decision.value,
                "reason": verdict.reason,
                "rule": verdict.rule,
            },
        )
    try:
        result = await c.hub.invoke(
            device_id, DeviceAction(action=request.action, args=request.args, reason=request.reason)
        )
    except ThursdayError as exc:
        raise HTTPException(status_code=409, detail=exc.to_dict()) from exc
    return result.model_dump(mode="json")


@router.websocket("/device")
async def device_socket(websocket: WebSocket) -> None:
    """TNP/1. The node dials out, so no machine has to expose a listening port."""
    container: Container = websocket.app.state.container
    await websocket.accept()
    session: WebSocketDeviceSession | None = None
    heartbeat: asyncio.Task | None = None

    try:
        hello_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        frame = parse_frame(hello_raw)
        if not isinstance(frame, Hello):
            await websocket.send_text(
                ErrorFrame(
                    code="protocol_error", message="expected HELLO", fatal=True
                ).model_dump_json()
            )
            await websocket.close(code=4400)
            return

        # A node is what actually runs commands on the owner's machine and reports whether
        # they worked. An impostor could act; worse, it could report `verified: true` for
        # something it never did, which is the one property everything else rests on. So
        # the signature is *checked*, and a failure closes the socket in every environment
        # — a development build that trusts anything is a development build that teaches
        # you the system is safe when it is not (T3).
        outcome = container.device_auth.verify(
            device_id=str(frame.device_id),
            name=frame.name,
            os=frame.os,
            nonce=frame.nonce,
            issued_at=frame.ts,
            signature=frame.signature,
        )
        if not outcome.ok:
            # The reason goes to the log for the operator and to the node in general terms;
            # telling an unauthenticated caller *which* check failed helps only an attacker.
            log.warning(
                "device_hello_rejected",
                device=frame.name,
                device_id=str(frame.device_id),
                reason=outcome.reason,
            )
            await websocket.send_text(
                ErrorFrame(
                    code="unauthenticated",
                    message="device authentication failed",
                    fatal=True,
                ).model_dump_json()
            )
            await websocket.close(code=4401)
            return

        session = WebSocketDeviceSession(websocket, frame)
        summary = await container.hub.register(session)
        await websocket.send_text(
            Welcome(session_id=summary.id, policy={"heartbeat_s": 15.0}).model_dump_json()
        )

        while True:
            raw = await websocket.receive_text()
            incoming = parse_frame(raw)
            if isinstance(incoming, ActionResultFrame):
                session.deliver(incoming)
            elif isinstance(incoming, Heartbeat):
                await container.hub.heartbeat(session.device_id, incoming.telemetry)
            # EVENT frames from nodes feed the event engine in Phase 2.

    except (TimeoutError, WebSocketDisconnect):
        pass
    except Exception as exc:
        log.warning("device_socket_error", error=str(exc))
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
        if session is not None:
            await container.hub.unregister(session.device_id)
