"""Device endpoints and the TNP/1 WebSocket (§9, §21, §22)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from thursday_core.container import Container
from thursday_core.logging import get_logger
from thursday_devices.hub import WebSocketDeviceSession
from thursday_security.pairing import PairingError, initial_trust
from thursday_shared.enums import PolicyDecision, TrustLevel
from thursday_shared.errors import ThursdayError
from thursday_shared.models import ActionRequest, DeviceAction, DeviceCapabilities
from thursday_shared.protocol import (
    CLOSE_SESSION_EXPIRED,
    ActionResultFrame,
    ErrorFrame,
    Heartbeat,
    Hello,
    Welcome,
    parse_frame,
)

from thursday_api.deps import get_container
from thursday_api.schemas import (
    CredentialRotation,
    DeviceActionRequest,
    DeviceHeartbeat,
    DeviceRegistration,
    PairingComplete,
    PairingStart,
)

log = get_logger(__name__)
router = APIRouter(tags=["devices"])


@router.get("/devices")
async def list_devices(c: Container = Depends(get_container)) -> dict:
    return {"devices": [d.model_dump(mode="json") for d in c.hub.all()]}


# Declared before `/devices/{device_id}`, and it has to stay there: FastAPI matches routes in
# declaration order, so with the parameterised route first this one is never reached — the
# literal "credentials" is taken as a device id and the request 422s. Appearing in the OpenAPI
# schema is not the same as being reachable, which is how this went unnoticed once already.
@router.get("/devices/credentials")
async def list_credentials(
    include_revoked: bool = False, c: Container = Depends(get_container)
) -> dict:
    """Which devices hold an identity, which key, and how old it is (§117, §133).

    `rotation_due` is a statement about hygiene, not about access. A device on this list
    still works, and that is the design position of ADR 0042: a key that expired on its own
    would lock the owner out of their own machines on a timer.
    """
    max_age = timedelta(days=c.settings.device_credential_max_age_days)
    return {
        "credentials": [
            {
                "device_id": str(cred.device_id),
                "name": cred.name,
                "os": cred.os,
                "fingerprint": cred.fingerprint,
                "algorithm": cred.algorithm,
                "paired_at": cred.paired_at.isoformat(),
                "revoked_at": cred.revoked_at.isoformat() if cred.revoked_at else None,
                "rotated_at": cred.rotated_at.isoformat() if cred.rotated_at else None,
                "key_age_days": cred.age().days,
                "rotation_due": cred.due_for_rotation(max_age),
            }
            for cred in c.pairing.credentials(include_revoked=include_revoked)
        ]
    }


# Declared above `/devices/{device_id}` for the same reason `/devices/credentials` is:
# FastAPI matches in declaration order, and "compute" would otherwise be read as a device id.
@router.get("/devices/compute")
async def list_compute(c: Container = Depends(get_container)) -> dict:
    """Every machine Thursday can run a model on, and what each one holds (ADDENDUM §2–§5).

    This is the acceptance criterion for local AI discovery — "Thursday lists available local
    models without manual configuration" — answered from what nodes reported at HELLO rather
    than from a configuration file somebody maintains by hand.

    A device with no `compute` is one that never reported an inventory: an older node, or a
    machine with no runtime installed. It is listed with `can_run_models: false` rather than
    omitted, because "this machine cannot run models" and "this machine does not exist" are
    different answers to "where can Thursday think?".
    """
    devices = []
    for summary in c.hub.all():
        profile = summary.compute
        devices.append(
            {
                "device_id": str(summary.id),
                "name": summary.name,
                "status": summary.status,
                "can_run_models": bool(summary.models),
                "compute": profile.model_dump(mode="json") if profile else None,
                "load": summary.load.model_dump(mode="json") if summary.load else None,
                "models": [m.model_dump(mode="json") for m in summary.models],
                "ai_capabilities": sorted(
                    cap for cap in summary.capabilities.granted if cap.startswith("ai")
                ),
            }
        )
    return {"devices": devices}


@router.get("/compute/benchmarks")
async def list_benchmarks(c: Container = Depends(get_container)) -> dict:
    """What real calls have measured about each model (ADDENDUM §25, §26).

    Measured from work Thursday actually did, not from a benchmark prompt nobody asked for.
    A model with too few samples is reported as `measured: false` rather than given a
    provisional figure — the router cannot tell a guess from a measurement, so it is not
    handed one.
    """
    return c.benchmarks.report()


@router.get("/compute/route")
async def explain_route(
    capability: str = "ai.llm",
    sensitivity: str = "PRIVATE",
    profile: str | None = None,
    mode: str | None = None,
    heavy: bool = False,
    c: Container = Depends(get_container),
) -> dict:
    """Where would this work go, and why (ADDENDUM §7–§9)?

    A dry run. It chooses nothing and runs nothing — it answers the question §44 says the
    owner should never have to ask, for the times somebody does: "why did that go to the
    laptop?". A router whose decisions cannot be inspected is one nobody can debug, and
    routing is exactly the kind of logic that goes subtly wrong for months.

    On failure it returns the rejections rather than a bare 404, because "nothing could run
    this" is only useful with the reasons attached (§38).
    """
    from thursday_core.compute_router import (
        ComputeRequest,
        NoComputeAvailable,
        RoutingMode,
        RoutingProfile,
    )
    from thursday_shared.enums import DataSensitivity

    try:
        request = ComputeRequest(
            capability=capability,
            sensitivity=DataSensitivity[sensitivity.upper()],
            profile=RoutingProfile(profile or c.settings.ai_routing_profile),
            mode=RoutingMode(mode or c.settings.ai_routing_mode),
            heavy=heavy,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"unknown routing option: {exc}") from exc

    try:
        target = c.compute_router.choose(request)
    except NoComputeAvailable as exc:
        return {
            "routed": False,
            "capability": capability,
            "reason": exc.message,
            "rejected": exc.details.get("rejected", []),
        }

    return {
        "routed": True,
        "target": {
            "device_id": str(target.device_id) if target.device_id else None,
            "runtime": str(target.runtime),
            "model": target.model,
            "local": target.local,
            "reasons": list(target.reasons),
        },
        "fallback": [
            {"device_id": str(f.device_id) if f.device_id else None, "model": f.model}
            for f in target.fallback
        ],
    }


@router.get("/models")
async def list_models(
    capability: str | None = None,
    include_offline: bool = True,
    c: Container = Depends(get_container),
) -> dict:
    """Which model exists on which machine (ADDENDUM §5) — the registry's acceptance test.

    Distinct from `GET /devices/compute`, which reports what each machine says *right now*.
    This is what Thursday remembers, including machines that are switched off and including
    the owner's corrections to what discovery guessed.
    """
    registry = c.model_registry
    rows = (
        registry.for_capability(capability, usable_only=not include_offline)
        if capability
        else registry.all(include_offline=include_offline)
    )
    return {
        "models": [
            {
                "id": str(m.id),
                "device_id": str(m.device_id) if m.device_id else None,
                "name": m.name,
                "runtime": str(m.observed.runtime),
                "kind": str(m.kind),
                "guessed_kind": str(m.observed.kind),
                "corrected": m.kind_override is not None,
                "capability": m.capability,
                "online": m.online,
                "enabled": m.enabled,
                "usable": m.usable,
                "context_length": m.observed.context_length,
                "required_vram": m.observed.required_vram_bytes,
                "note": m.note,
            }
            for m in rows
        ],
        "health": registry.health(),
    }


@router.post("/models/{model_id}/kind")
async def correct_model_kind(
    model_id: UUID, kind: str | None = None, c: Container = Depends(get_container)
) -> dict:
    """Tell Thursday what a model is actually for, or clear the correction.

    Discovery guesses from the name, and a private build called `house-model-v3` is
    unreadable. The correction is stored separately from the observation and survives the
    node reconnecting — a fix that the next reconnect undoes is worse than no fix, because
    the owner watched it work.
    """
    from thursday_shared.compute import ModelKind

    try:
        entry = await c.model_registry.set_kind(model_id, ModelKind(kind) if kind else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"unknown model kind: {kind}") from exc
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": str(entry.id), "kind": str(entry.kind), "guessed": str(entry.observed.kind)}


@router.post("/models/{model_id}/enabled")
async def set_model_enabled(
    model_id: UUID, enabled: bool | None = None, c: Container = Depends(get_container)
) -> dict:
    """Switch a model off, on, or back to having no opinion.

    Tri-state deliberately: "never asked" and "the owner said no" are different facts, and
    collapsing them would make a default look like a decision.
    """
    try:
        entry = await c.model_registry.set_enabled(model_id, enabled)
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": str(entry.id), "enabled": entry.enabled, "usable": entry.usable}


@router.get("/devices/{device_id}")
async def get_device(device_id: UUID, c: Container = Depends(get_container)) -> dict:
    summary = c.hub.summary(device_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown device")
    return summary.model_dump(mode="json")


@router.post("/devices/pair/start")
async def start_pairing(request: PairingStart, c: Container = Depends(get_container)) -> dict:
    """A node asks to pair, proving it holds the key it offers (§80).

    Returns a short-lived code for the node to display. The code is not a credential — it
    authorises one enrolment briefly, and what actually gets stored is the public key.
    """
    try:
        pending = c.pairing.start(
            public_key=request.public_key,
            name=request.name,
            os=request.os,
            hostname=request.hostname,
            nonce=request.nonce,
            issued_at=request.issued_at,
            signature=request.signature,
            caller=request.name,
        )
    except PairingError as exc:
        # 400 rather than 401: nothing here is authenticated yet, and the caller needs to
        # know their request was malformed rather than that they were rejected.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "pairing_code": pending.code,
        "device_id": str(pending.device_id),
        "expires_at": pending.expires_at.isoformat(),
    }


@router.post("/devices/pair/complete")
async def complete_pairing(request: PairingComplete, c: Container = Depends(get_container)) -> dict:
    """The owner confirms the code shown on the device (§80).

    This is the proof that a *person* wants this device paired. Proof of possession alone,
    at `pair/start`, would mean any process that can reach the API can enrol itself.
    """
    try:
        credential = c.pairing.complete(request.code)
    except PairingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = await c.hub.enrol(
        device_id=credential.device_id,
        name=credential.name,
        kind=request.device_type or "desktop",
        os=credential.os,
        capabilities=DeviceCapabilities(),
    )
    c.hub.set_trust(credential.device_id, initial_trust(credential))
    return {
        "device_id": str(credential.device_id),
        "fingerprint": credential.fingerprint,
        # LIMITED, not TRUSTED. Pairing a laptop and authorising it to drive the server are
        # separate decisions (ADR 0024); the owner raises trust deliberately.
        "trust_level": int(summary.trust_level),
        "paired_at": credential.paired_at.isoformat(),
    }


@router.post("/devices/{device_id}/rotate")
async def rotate_credential(
    device_id: UUID, request: CredentialRotation, c: Container = Depends(get_container)
) -> dict:
    """Let a paired node replace its own key without a person re-pairing it (§117).

    Unauthenticated in the session sense, and that is not an oversight: the two signatures
    in the body *are* the authentication, and they are stronger than a session would be.
    The request is authorised by the private key the core already trusts for this device,
    which is the same thing every HELLO from it proves.

    The live session is dropped on success. It was authenticated with the key that has just
    been retired, so leaving it up would let the old key keep driving the machine for as
    long as the connection lasted — which would make rotation a change of record-keeping
    rather than a change of access.
    """
    try:
        credential = c.pairing.rotate(
            device_id,
            new_public_key=request.new_public_key,
            signature_by_old=request.signature_by_old,
            signature_by_new=request.signature_by_new,
            nonce=request.nonce,
            issued_at=request.issued_at,
        )
    except PairingError as exc:
        # 403 rather than 404 for an unknown device: the caller is presenting signatures,
        # and "no such device" versus "bad signature" is a distinction worth denying them.
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # `unregister`, not `forget`: this device is coming straight back with its new key, and
    # forgetting it would drop the trust level the owner granted and list it as gone.
    await c.hub.unregister(device_id)
    return {
        "device_id": str(device_id),
        "fingerprint": credential.fingerprint,
        "rotated_at": credential.rotated_at.isoformat() if credential.rotated_at else None,
    }


@router.put("/devices/{device_id}/wake-on-lan")
async def set_wake_on_lan(
    device_id: UUID, mac: str, enabled: bool = False, c: Container = Depends(get_container)
) -> dict:
    """Record where a machine is, so it can be woken (ADDENDUM §20).

    Set by the owner, never learned from the network. Thursday sniffing MAC addresses would
    be the same reconnaissance ADR 0044 refused for inference endpoints, and a magic packet
    sent to an address Thursday guessed is a packet aimed at somebody else's machine.

    `enabled` defaults to False: recording an address and consenting to use it are separate
    decisions, and the second one should be made deliberately rather than as a side effect
    of the first.
    """
    from thursday_devices.wake import InvalidMac, WakeRecord, magic_packet

    try:
        magic_packet(mac)
    except InvalidMac as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record = WakeRecord(device_id=device_id, mac=mac.strip(), enabled=enabled)
    c.wake_records[device_id] = record
    return record.row()


@router.post("/devices/{device_id}/wake")
async def wake_device(device_id: UUID, c: Container = Depends(get_container)) -> dict:
    """Wake a sleeping machine (ADDENDUM §20).

    The order here is §20's own: policy check, then packet, then *wait for the node*. The
    third step is what makes the answer honest — a magic packet is unacknowledged UDP, so
    "sent" and "woke" are separate facts and only the second one is what the owner asked
    about.

    Gated by the Permission Engine like every other consequential verb. There is no
    back door around it, and this endpoint is not one (§95).
    """
    record = c.wake_records.get(device_id) if c.wake_records else None
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="no MAC address is recorded for this device; set one before waking it",
        )
    if not record.enabled:
        # Having an address and being willing to use it are two decisions. Refusing here
        # rather than at the policy layer keeps "the owner turned this off" distinct from
        # "the owner has not approved this one", which are different things to tell them.
        raise HTTPException(status_code=409, detail="waking is disabled for this device")

    from thursday_devices import actions as catalogue

    spec = catalogue.get("device.wake")
    if spec is None:  # pragma: no cover - the catalogue is a constant in this build
        # Stated rather than assumed. If the verb ever leaves the catalogue this says so;
        # without it the next line raises "NoneType has no attribute 'level'", which sends
        # whoever is reading the traceback looking in the wrong place entirely.
        raise HTTPException(status_code=500, detail="device.wake is missing from the catalogue")

    verdict = c.permissions.decide(
        ActionRequest(
            action="device.wake",
            resource=record.mac,
            device_id=device_id,
            level=spec.level,
            risk=spec.risk,
            reversible=spec.reversible,
            expected_outcome="the machine comes online and can run work",
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

    result = await c.wake.wake(device_id, record.mac)
    return {
        "device_id": str(device_id),
        "sent": result.sent,
        # The field that matters, and the one that is not inferred from sending.
        "verified": result.verified,
        "waited_s": round(result.waited_s, 1),
        "error": result.error,
    }


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: UUID, c: Container = Depends(get_container)) -> dict:
    """Withdraw a device's identity, and disconnect it if it is currently attached.

    The credential record is kept rather than deleted: "revoked on Tuesday" is a fact
    somebody will need, and a deleted credential would let the device pair again as though
    nothing had happened.
    """
    credential = c.pairing.revoke(device_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="unknown device")
    # `forget`, not `unregister`: a disconnected device is one that is coming back and the
    # owner wants to see it listed as away. A revoked one is not — it re-pairs under a new
    # identity — so leaving the summary behind would keep a trust level nobody re-granted.
    await c.hub.forget(device_id)
    return {
        "device_id": str(device_id),
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
    }


@router.post("/devices/{device_id}/trust")
async def set_device_trust(
    device_id: UUID, level: int, c: Container = Depends(get_container)
) -> dict:
    """Set how far a device is trusted to drive *other* devices (§9.4, V8).

    The owner's decision and nobody else's. A node cannot set this for itself — it is not
    sent at HELLO and is not read from one — because a device asserting its own trust level
    is a device granting itself permission.
    """
    try:
        trust = TrustLevel(level)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"trust level must be one of {[int(t) for t in TrustLevel]}",
        ) from exc
    summary = c.hub.set_trust(device_id, trust)
    if summary is None:
        raise HTTPException(status_code=404, detail="unknown device")
    return summary.model_dump(mode="json")


@router.get("/devices/output/follow-me")
async def follow_me(c: Container = Depends(get_container)) -> dict:
    """Which device an answer would be spoken on right now (§9 follow-me).

    Exposed because "why did that come out of the kitchen speaker" is otherwise
    unanswerable, and a routing heuristic nobody can inspect is a routing heuristic nobody
    can correct.
    """
    world = c.world.snapshot()
    device = c.device_router.follow_me(world=world, origin_device_id=world.active_device_id)
    return {
        "device": device.model_dump(mode="json") if device else None,
        "reason": "the machine the owner most recently used that can play audio",
    }


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

        # §79. One HELLO used to authenticate a connection for as long as it happened to
        # stay up — days, on a machine that never sleeps. That is the gap this closes, and
        # the reason it matters is rotation: a session authenticated with a key that has
        # since been replaced would outlive the key, and rotation that ends nothing is not
        # rotation. The deadline is told to the node so it can reconnect a moment early
        # rather than discovering it mid-action.
        max_session = timedelta(hours=container.settings.device_session_max_hours)
        deadline = datetime.now(UTC) + max_session
        await websocket.send_text(
            Welcome(
                session_id=summary.id,
                policy={
                    "heartbeat_s": 15.0,
                    "session_expires_at": deadline.isoformat(),
                    "session_max_s": max_session.total_seconds(),
                },
            ).model_dump_json()
        )

        while True:
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                # Not an error, and said so: a node that treats a routine expiry as a
                # failure is a machine the owner silently loses.
                log.info(
                    "device_session_expired",
                    device=session.name,
                    device_id=str(session.device_id),
                    after_s=round(max_session.total_seconds()),
                )
                await websocket.send_text(
                    ErrorFrame(
                        code="session_expired",
                        message="session reached its maximum age; reconnect with a new HELLO",
                        fatal=False,
                    ).model_dump_json()
                )
                await websocket.close(code=CLOSE_SESSION_EXPIRED)
                return

            # Bounded by whatever comes first: the next frame, or the deadline. Waiting on
            # `receive_text` alone would hold a session open past its expiry for as long as
            # the node stayed quiet, which is exactly the case the bound is for.
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=remaining)
            except TimeoutError:
                # The deadline arrived while the node had nothing to say — the ordinary
                # case, since a quiet node is a healthy one. Loop round rather than falling
                # through to the handler's `except TimeoutError`, which would tear the
                # socket down with the close code that means "do not come back". Telling a
                # node it was cut off when its session merely aged is how a machine that
                # should have reconnected in a second stays gone.
                continue
            incoming = parse_frame(raw)
            if isinstance(incoming, ActionResultFrame):
                session.deliver(incoming)
            elif isinstance(incoming, Heartbeat):
                await container.hub.heartbeat(
                    session.device_id, incoming.telemetry, load=incoming.load
                )
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
