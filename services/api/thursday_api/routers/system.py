"""System endpoints: health, world state, audit, undo, emergency stop, backups."""

from __future__ import annotations

from enum import IntEnum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from thursday_core.backup import BackupError
from thursday_core.container import Container
from thursday_security.policy import HARD_BLOCKED
from thursday_shared.enums import (
    AutonomyLevel,
    PermissionLevel,
    PolicyDecision,
    ProactivityLevel,
    RiskLevel,
)
from thursday_shared.errors import ThursdayError
from thursday_shared.models import ActionRequest, utcnow

from thursday_api.deps import get_container
from thursday_api.schemas import EmergencyStopRequest

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(c: Container = Depends(get_container)) -> dict:
    checks = await c.health()
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def _subset(checks: list[dict], prefix: str) -> dict:
    """PART 91's per-component endpoints, so a probe can target one dependency."""
    matching = [check for check in checks if check["component"].startswith(prefix)]
    return {
        "ok": bool(matching) and all(check["ok"] for check in matching),
        "checks": matching,
    }


@router.get("/health/database")
async def health_database(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "database")


@router.get("/health/redis")
async def health_redis(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "redis")


@router.get("/health/devices")
async def health_devices(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "devices")


@router.get("/health/models")
async def health_models(c: Container = Depends(get_container)) -> dict:
    return _subset(await c.health(), "model")


@router.get("/world-state")
async def world_state(c: Container = Depends(get_container)) -> dict:
    """PART 45. The 'now' Thursday reasons against."""
    return c.world.snapshot().model_dump(mode="json")


@router.get("/world")
async def world(c: Container = Depends(get_container)) -> dict:
    return c.world.snapshot().model_dump(mode="json")


@router.get("/autonomy")
async def get_autonomy(c: Container = Depends(get_container)) -> dict:
    """PART 97. Two dials, deliberately separate: one for acting, one for speaking."""
    return {
        "autonomy": c.permissions.autonomy.name,
        "autonomy_level": int(c.permissions.autonomy),
        "proactivity": c.automations.gate.level.name,
        "proactivity_level": int(c.automations.gate.level),
        "note": (
            "raising autonomy relaxes ASK_ONCE actions only; ASK_ALWAYS and BLOCK are "
            "unaffected at every level"
        ),
    }


def _level[T: IntEnum](enum: type[T], value: str) -> T:
    """Accept the name this API prints as well as the number behind it.

    ``GET /autonomy`` reports ``"MODERATE"``. Requiring ``2`` on the way back in would mean
    the value you are handed is not a value you can send.
    """
    try:
        return enum[value.upper()] if not value.lstrip("-").isdigit() else enum(int(value))
    except (KeyError, ValueError) as exc:
        allowed = ", ".join(member.name for member in enum)
        raise HTTPException(
            status_code=400, detail=f"unknown level {value!r}; try {allowed}"
        ) from exc


@router.post("/autonomy")
async def set_autonomy(
    autonomy: str | None = None,
    proactivity: str | None = None,
    c: Container = Depends(get_container),
) -> dict:
    if autonomy is not None:
        c.permissions.set_autonomy(_level(AutonomyLevel, autonomy))
    if proactivity is not None:
        c.automations.gate.level = _level(ProactivityLevel, proactivity)
    return await get_autonomy(c)


@router.get("/agents")
async def agents(c: Container = Depends(get_container)) -> dict:
    return {
        "agents": [
            {
                **spec.model_dump(mode="json"),
                "success_rate": round(c.agents.success_rate(spec.name), 3),
            }
            for spec in c.agents.specs()
        ]
    }


@router.get("/tools")
async def tools(c: Container = Depends(get_container)) -> dict:
    return {"tools": [spec.model_dump(mode="json") for spec in c.tools.specs()]}


@router.get("/policies")
async def policies(c: Container = Depends(get_container)) -> dict:
    """PART 70. Every action Thursday knows, and what it will do when asked to take it.

    The decision reported here is the *effective* one: the table's default with the current
    autonomy level already applied, so what the panel shows is what will actually happen.
    """
    autonomy = c.permissions.autonomy
    table = c.permissions.policy
    rows = []
    for action in table.known_actions():
        policy = table.get(action, autonomy=autonomy)
        rows.append(
            {
                "action": action,
                "namespace": action.split(".")[0],
                "decision": policy.default.value,
                "level": policy.level.name,
                "risk": policy.risk.value,
                "reversible": policy.reversible,
                "requires_backup": policy.requires_backup,
                "bulk_threshold": policy.bulk_threshold,
                "blocked": table.is_blocked(action),
                # An ASK_ALWAYS action can be tightened but never loosened (ADR 0008), so the
                # panel greys out the control instead of offering a choice that would not stick.
                "can_relax": table.can_relax(action),
            }
        )
    return {"autonomy": autonomy.name, "policies": rows, "hard_blocked": sorted(HARD_BLOCKED)}


@router.post("/policies/{action}")
async def set_policy(
    action: str, decision: PolicyDecision, c: Container = Depends(get_container)
) -> dict:
    """Change one action's approval mode.

    A request the table would silently ignore is refused instead. A setting that appears to
    save and then does nothing is worse than an error: the owner would believe deleting files
    now happens without asking, and it would not.
    """
    table = c.permissions.policy
    try:
        table.override(action, decision)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    effective = table.get(action, autonomy=c.permissions.autonomy)
    if effective.default is not decision:
        table.clear_override(action)
        raise HTTPException(
            status_code=400,
            detail=(
                f"{action!r} cannot be set to {decision.value}: it stays "
                f"{effective.default.value} because it is a system-level or "
                "ask-every-time action"
            ),
        )
    return {"action": action, "decision": effective.default.value}


@router.get("/audit")
async def audit(
    task_id: UUID | None = None,
    trace_id: str | None = None,
    device_id: UUID | None = None,
    actor: str | None = None,
    tool: str | None = None,
    limit: int = 100,
    c: Container = Depends(get_container),
) -> dict:
    """The trail, filterable by the identifiers a person actually has in hand.

    ``trace_id`` is the one printed in every log line for a request, so it is the bridge
    from "this reply looked wrong" to every action that produced it.
    """
    entries = c.audit.entries(
        task_id=task_id,
        trace_id=trace_id,
        device_id=device_id,
        actor=actor,
        tool=tool,
        limit=limit,
    )
    return {
        "entries": [e.model_dump(mode="json") for e in entries],
        "chain_intact": c.audit.verify_chain(),
    }


@router.get("/undo")
async def undo_list(c: Container = Depends(get_container)) -> dict:
    return {"undoable": [u.model_dump(mode="json") for u in c.undo.pending()]}


@router.post("/undo/{action_id}")
async def undo(action_id: UUID, c: Container = Depends(get_container)) -> dict:
    try:
        return {"undone": await c.undo.undo(action_id)}
    except ThursdayError as exc:
        raise HTTPException(status_code=404, detail=exc.to_dict()) from exc


@router.post("/emergency/stop")
async def emergency_stop(
    request: EmergencyStopRequest, c: Container = Depends(get_container)
) -> dict:
    """§69. A plain endpoint on purpose — it must work when the model is down."""
    return {"scope": request.scope, "actions": await c.emergency_stop(request.scope)}


@router.post("/emergency/release")
async def release_lockdown(c: Container = Depends(get_container)) -> dict:
    c.permissions.set_lockdown(False)
    return {"lockdown": False}


# ------------------------------------------------------------------ voice (V4)


@router.get("/voice")
async def voice_state(c: Container = Depends(get_container)) -> dict:
    """What the voice loop is doing, and where it would speak.

    ``listening`` is the one a UI must trust: it is true exactly when the microphone is
    capturing, so the recording indicator drawn from it is never wrong.
    """
    return c.voice.snapshot()


@router.post("/voice/interrupt")
async def voice_interrupt(c: Container = Depends(get_container)) -> dict:
    """ "Thursday หยุด", as an endpoint.

    Plain and model-free on purpose (§69): the reason to reach for this is often that
    reasoning is what went wrong.
    """
    cut = await c.voice.interrupt(reason="interrupt requested")
    return {
        "interrupted": cut is not None,
        "spoken": cut.partial if cut else "",
        "unspoken": cut.unspoken if cut else "",
    }


@router.post("/voice/output")
async def voice_output(
    device_id: str | None = None,
    follow_me: bool | None = None,
    c: Container = Depends(get_container),
) -> dict:
    """Choose where Thursday speaks, or let output follow the owner between devices."""
    if follow_me is not None:
        c.audio_router.follow_me = follow_me
    if device_id is not None:
        try:
            c.audio_router.prefer(device_id or None)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return c.audio_router.snapshot()


# ------------------------------------------------------------------ vision (V6)


@router.get("/vision")
async def vision_state(c: Container = Depends(get_container)) -> dict:
    """What the camera is doing, and what has been seen.

    ``indicator_on`` is the field a UI must draw its camera light from: it is derived from
    the same state the capture path checks, so it cannot disagree with reality.
    """
    return c.vision.snapshot()


@router.get("/vision/camera/log")
async def camera_log(limit: int = 20, c: Container = Depends(get_container)) -> dict:
    """ "When was my camera on?" — answerable by the owner, not by a support ticket."""
    return {"entries": c.camera.recent_log(limit)}


@router.post("/vision/camera/grant")
async def grant_camera(
    reason: str,
    seconds: float | None = None,
    max_captures: int | None = None,
    c: Container = Depends(get_container),
) -> dict:
    """Permit the camera, for a stated reason and a bounded window (§51).

    A reason is required. A grant nobody can describe later is a grant nobody can audit,
    and the owner reading their own camera log deserves to see why rather than a timestamp.
    """
    try:
        c.camera.grant_access(reason, seconds=seconds, max_captures=max_captures)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return c.camera.snapshot()


@router.post("/vision/camera/off")
async def camera_off(c: Container = Depends(get_container)) -> dict:
    """ "ปิดกล้อง". Plain and model-free, like every other emergency control (§69)."""
    await c.camera.revoke(why="the owner turned it off")
    return c.camera.snapshot()


@router.get("/vision/objects")
async def seen_objects(c: Container = Depends(get_container)) -> dict:
    """What has been seen, as sightings — never as claims about where things are now."""
    return {
        "objects": [
            {
                "label": o.label,
                "object_type": o.object_type,
                "camera_id": o.camera_id,
                "location_context": o.location_context,
                "confidence": round(o.confidence, 3),
                "first_seen": o.first_seen.isoformat(),
                "last_seen": o.last_seen.isoformat(),
                "sightings": o.sightings,
                "description": o.describe("en"),
            }
            for o in c.spatial.objects()
        ]
    }


@router.delete("/vision/objects")
async def forget_sightings(c: Container = Depends(get_container)) -> dict:
    """Wipe what was seen. Part of the privacy controls (§68)."""
    return {"forgotten": c.spatial.forget_all()}


# ------------------------------------------------------------------ gestures (V7)


@router.get("/gestures")
async def gesture_state(c: Container = Depends(get_container)) -> dict:
    """Whether hand movement is being read as commands at all (§28).

    ``watching`` is the field a UI draws its indicator from: an ordinary wave is not a
    command, and the owner should be able to see when that stops being true.
    """
    return c.gesture_mode.snapshot()


@router.post("/gestures/open")
async def open_gestures(c: Container = Depends(get_container)) -> dict:
    c.gesture_mode.open()
    return c.gesture_mode.snapshot()


@router.post("/gestures/close")
async def close_gestures(c: Container = Depends(get_container)) -> dict:
    """ "หยุดรับท่าทาง". Closing is always available and never refused (§69)."""
    c.gesture_mode.close()
    c.gesture_tracker.reset()
    return c.gesture_mode.snapshot()


# ------------------------------------------------------------------ cost (§61, §133)


@router.get("/costs")
async def costs(c: Container = Depends(get_container)) -> dict:
    """What Thursday has spent, and how close it is to the ceiling.

    Exposed because a limit the owner cannot watch approaching is one they only learn about
    when it binds — and the useful moment is before that, while they can still decide
    whether the work is worth raising it for.
    """
    return c.costs.summary()


@router.get("/costs/detail")
async def cost_detail(days: int = 30, c: Container = Depends(get_container)) -> dict:
    """Spend broken out by day, provider and recent call. For the dashboard (§133)."""
    return {
        **c.costs.summary(),
        "by_day": {
            str(day): round(usd, 4) for day, usd in sorted(c.costs.by_day(days=days).items())
        },
        "recent": [
            {
                "at": charge.at.isoformat(),
                "provider": charge.provider,
                "tier": charge.tier,
                "tokens": charge.tokens,
                "usd": round(charge.usd, 6),
                "agent": charge.agent,
                "task_id": str(charge.task_id) if charge.task_id else None,
            }
            for charge in reversed(c.costs.charges(limit=50))
        ],
    }


# ------------------------------------------------------------------ backup (Sprint 47)


@router.get("/backups")
async def list_backups(c: Container = Depends(get_container)) -> dict:
    """What is on disk, newest first, each with whether it actually verifies.

    The verification is done here rather than reported from the manifest: a manifest that
    says a backup is fine is the part an editor would fix first, and "is my backup any good"
    should be answerable on a quiet Tuesday rather than during the emergency.
    """
    directory = c.settings.data_dir / "backups"
    entries = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            entries.append({"name": path.name, **c.backups.inspect(path)})
        except BackupError as exc:
            entries.append({"name": path.name, "unreadable": str(exc)})
    return {"backups": entries, "directory": str(directory), "components": c.backups.components}


@router.post("/backups")
async def create_backup(note: str = "", c: Container = Depends(get_container)) -> dict:
    """Take a backup now. Reading state and writing a file the owner asked for."""
    directory = c.settings.data_dir / "backups"
    path = directory / f"thursday-{utcnow():%Y%m%d-%H%M%S}.json"
    manifest = c.backups.create(path, note=note)
    return {"path": str(path), "name": path.name, **manifest.to_dict()}


@router.post("/backups/{name}/restore")
async def restore_backup(name: str, c: Container = Depends(get_container)) -> dict:
    """Replace everything Thursday holds with what is in this archive.

    Goes through the Permission Engine like any other destructive act — there is no back
    door around it for administration, which is precisely the kind of caller that would be
    given one. `system.restore` resolves to the `system` namespace: SYSTEM level,
    ASK_ALWAYS, and not something an override can turn into AUTO.
    """
    path = (c.settings.data_dir / "backups" / name).resolve()
    directory = (c.settings.data_dir / "backups").resolve()
    if directory not in path.parents:
        # The name comes off a URL. Without this, `../../etc/passwd` is a restore source.
        raise HTTPException(status_code=400, detail="that is not a backup in this directory")

    verdict = c.permissions.decide(
        ActionRequest(
            action="system.restore",
            resource=name,
            level=PermissionLevel.SYSTEM,
            risk=RiskLevel.CRITICAL,
            reversible=False,
            expected_outcome=(
                "replace every memory, task, audit entry, credential and preference "
                f"with the contents of {name}"
            ),
        )
    )
    if verdict.decision is not PolicyDecision.AUTO:
        raise HTTPException(
            status_code=403,
            detail={
                "decision": verdict.decision.value,
                "reason": verdict.reason,
                "rule": verdict.rule,
                "restoring": c.backups.inspect(path),
            },
        )

    try:
        restored = c.backups.restore(path, confirm=True)
    except BackupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"restored": restored, "name": name}


@router.get("/backups/{name}/verify")
async def verify_backup(name: str, c: Container = Depends(get_container)) -> dict:
    path = c.settings.data_dir / "backups" / name
    try:
        problems = c.backups.verify(path)
    except BackupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"name": name, "ok": not problems, "problems": problems}


# ------------------------------------------------------------------ updates (§120, Sprint 48)


@router.get("/updates")
async def update_state(c: Container = Depends(get_container)) -> dict:
    """Whether a newer version exists, on the channel this deployment was configured with.

    A read. It cannot install anything, and it takes no parameters — in particular it takes
    no URL, which is the whole of §120 expressed as a function signature.
    """
    state = c.updates.check()
    return {
        "current": state.current,
        "latest": state.latest,
        "available": state.available,
        "critical": state.critical,
        "notes": state.notes,
        "checked_at": state.checked_at.isoformat() if state.checked_at else None,
        "problem": state.problem,
        "history": state.history,
    }


@router.post("/updates/apply")
async def apply_update(c: Container = Depends(get_container)) -> dict:
    """Install the latest release — through the Permission Engine, like anything destructive.

    Takes no body at all. What gets installed is whatever the *configured* channel offers,
    verified against the *configured* key; there is no argument through which a caller could
    name a version, a URL or a file. That is deliberate: this endpoint is exactly where an
    attacker would want a parameter.
    """
    state = c.updates.check()
    if state.problem:
        raise HTTPException(status_code=503, detail=state.problem)
    if not state.available:
        return {"applied": False, "reason": f"{state.current} is already the latest release"}

    verdict = c.permissions.decide(
        ActionRequest(
            action="system.update",
            resource=state.latest or "",
            level=PermissionLevel.SYSTEM,
            risk=RiskLevel.CRITICAL,
            reversible=False,
            expected_outcome=(
                f"replace Thursday {state.current} with {state.latest}, "
                "which is the code that enforces every other rule"
            ),
        )
    )
    if verdict.decision is not PolicyDecision.AUTO:
        raise HTTPException(
            status_code=403,
            detail={
                "decision": verdict.decision.value,
                "reason": verdict.reason,
                "rule": verdict.rule,
                "installing": {"version": state.latest, "notes": state.notes},
            },
        )
    # Unreachable while `system.update` is ASK_ALWAYS, and written out rather than assumed:
    # a policy that changes should not silently turn this into a no-op that reports success.
    raise HTTPException(
        status_code=501,
        detail="this build verifies updates but has no installer wired up",
    )


# ------------------------------------------------------------------ metrics (§128, Sprint 49)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(c: Container = Depends(get_container)) -> str:
    """Prometheus exposition. Numbers only, and deliberately dull ones.

    No label here carries a path, a resource, a filename or anything the owner typed. A
    monitoring system has none of Thursday's privacy controls, keeps data far longer than
    Thursday does, and is read by whoever runs the dashboard — so what leaves through this
    endpoint is bounded at the point the metric is declared, not filtered here.
    """
    return c.metrics.render()
