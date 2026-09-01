"""Permission engine, policy table, autonomy and grants (PART 18–21, PART 97)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.permissions import PermissionEngine
from thursday_security.policy import HARD_BLOCKED, PolicyTable, canonical
from thursday_security.privacy import PrivacyZone, PrivacyZoneRegistry
from thursday_shared.enums import (
    ApprovalScope,
    AutonomyLevel,
    DataSensitivity,
    PermissionLevel,
    PolicyDecision,
    RiskLevel,
)
from thursday_shared.ids import new_id
from thursday_shared.models import ActionRequest, PermissionGrant, PermissionSet


@pytest.fixture
def engine() -> PermissionEngine:
    return PermissionEngine()


# ------------------------------------------------------------------ the basic verdicts


def test_ordinary_reversible_work_is_automatic(engine):
    verdict = engine.decide(ActionRequest(action="app.open", resource="chrome"))
    assert verdict.decision is PolicyDecision.AUTO
    assert verdict.allowed
    assert verdict.rule == "policy_default_auto"


def test_modifying_an_existing_document_is_automatic_but_backed_up(engine):
    """PART 21 — 'AUTO with version backup' is a fact the verdict carries."""
    verdict = engine.decide(ActionRequest(action="file.write", resource="/home/u/report.md"))
    assert verdict.decision is PolicyDecision.AUTO
    assert verdict.requires_backup is True
    assert "backup" in verdict.reason


@pytest.mark.parametrize(
    "action", ["email.send", "message.send", "social.post", "purchase.make", "file.delete"]
)
def test_the_dangerous_set_is_asked_every_time(engine, action):
    verdict = engine.decide(ActionRequest(action=action))
    assert verdict.decision is PolicyDecision.ASK_ALWAYS
    assert verdict.needs_approval


def test_unknown_actions_fail_closed(engine):
    """An action nobody declared is asked every time, never AUTO."""
    verdict = engine.decide(ActionRequest(action="frobnicate.the_thing"))
    assert verdict.decision is PolicyDecision.ASK_ALWAYS


def test_an_unknown_verb_inherits_its_namespace(engine):
    """`file.compress` is unknown, but `file.*` still says what kind of thing it is."""
    policy = PolicyTable().get("file.compress")
    assert policy.level is PermissionLevel.MODIFY
    assert policy.default is PolicyDecision.ASK_ONCE


def test_legacy_flat_names_still_resolve():
    assert canonical("open_app") == "app.open"
    assert canonical("delete") == "file.delete"
    assert PolicyTable().get("send_email").default is PolicyDecision.ASK_ALWAYS


# ------------------------------------------------------------------ the block set


@pytest.mark.parametrize(
    "action", ["security.disable", "audit.modify", "permission.self_grant", "credential.export"]
)
def test_hard_blocked_actions_have_no_override_path(engine, action):
    assert action in HARD_BLOCKED
    assert engine.decide(ActionRequest(action=action)).decision is PolicyDecision.BLOCK

    with pytest.raises(PermissionError):
        engine.policy.override(action, PolicyDecision.AUTO)
    with pytest.raises(PermissionError):
        engine.add_grant(PermissionGrant(action=action))

    engine.set_autonomy(AutonomyLevel.HIGH)
    assert engine.decide(ActionRequest(action=action)).decision is PolicyDecision.BLOCK


def test_a_blocked_namespace_blocks_its_unknown_verbs(engine):
    """A new `audit.*` verb is blocked without anyone having to remember to add it."""
    assert engine.decide(ActionRequest(action="audit.truncate")).decision is PolicyDecision.BLOCK


# ------------------------------------------------------------------ overrides


def test_a_user_may_loosen_low_levels():
    policy = PolicyTable()
    policy.override("system.process.stop", PolicyDecision.AUTO)
    assert policy.get("system.process.stop").default is PolicyDecision.AUTO


def test_a_user_may_not_auto_approve_system_work_or_the_ask_always_set():
    policy = PolicyTable()
    policy.override("shell.admin", PolicyDecision.AUTO)
    assert policy.get("shell.admin").default is PolicyDecision.ASK_ALWAYS

    policy.override("file.delete", PolicyDecision.AUTO)
    assert policy.get("file.delete").default is PolicyDecision.ASK_ALWAYS


# ------------------------------------------------------------------ escalating conditions


def test_blast_radius_turns_an_automatic_action_into_a_question(engine):
    assert (
        engine.decide(ActionRequest(action="file.move", object_count=3)).decision
        is PolicyDecision.AUTO
    )
    verdict = engine.decide(ActionRequest(action="file.move", object_count=342))
    assert verdict.decision is PolicyDecision.ASK_ONCE
    assert verdict.rule == "blast_radius"


def test_irreversible_actions_are_asked_about_every_time(engine):
    verdict = engine.decide(ActionRequest(action="file.copy", reversible=False))
    assert verdict.decision is PolicyDecision.ASK_ALWAYS
    assert verdict.rule == "irreversible"


def test_high_risk_is_asked_about_even_at_a_low_level(engine):
    verdict = engine.decide(
        ActionRequest(action="app.open", risk=RiskLevel.HIGH, level=PermissionLevel.OPEN)
    )
    assert verdict.decision is PolicyDecision.ASK_ALWAYS


# ------------------------------------------------------------------ agent envelope


def test_permissions_intersect_and_never_widen():
    wide = PermissionSet(max_level=PermissionLevel.ADMIN, allowed_tools=["a", "b", "c"])
    narrow = PermissionSet(max_level=PermissionLevel.READ, allowed_tools=["b", "z"])
    combined = wide.intersect(narrow)
    assert combined.max_level is PermissionLevel.READ
    assert combined.allowed_tools == ["b"]


def test_an_agent_cannot_exceed_its_ceiling(engine):
    verdict = engine.decide(
        ActionRequest(action="file.write", resource="/home/u/a.txt"),
        permissions=PermissionSet(max_level=PermissionLevel.READ),
    )
    assert verdict.decision is PolicyDecision.BLOCK
    assert verdict.rule == "permission_ceiling"


def test_path_scopes_confine_an_agent(engine):
    permissions = PermissionSet(max_level=PermissionLevel.MODIFY, path_scopes=["/home/u/work/*"])
    assert (
        engine.decide(
            ActionRequest(action="file.write", resource="/home/u/work/report.md"),
            permissions=permissions,
        ).decision
        is PolicyDecision.AUTO
    )
    assert (
        engine.decide(
            ActionRequest(action="file.write", resource="/etc/passwd"), permissions=permissions
        ).decision
        is PolicyDecision.BLOCK
    )


# ------------------------------------------------------------------ grants


def test_grants_are_scoped_and_expire(engine):
    engine.add_grant(
        PermissionGrant(
            action="calendar.write", resource_glob="*@school.ac.th", scope=ApprovalScope.ALWAYS
        )
    )
    assert (
        engine.decide(ActionRequest(action="calendar.write", resource="dean@school.ac.th")).decision
        is PolicyDecision.AUTO
    )
    assert (
        engine.decide(
            ActionRequest(action="calendar.write", resource="someone@elsewhere.com")
        ).decision
        is PolicyDecision.ASK_ONCE
    )


def test_an_ask_always_action_can_never_become_a_grant(engine):
    """ADR 0008 — the rule is enforced in the engine, not in the UI."""
    with pytest.raises(PermissionError, match="ASK_ALWAYS"):
        engine.add_grant(PermissionGrant(action="file.delete", scope=ApprovalScope.ALWAYS))
    with pytest.raises(PermissionError, match="ASK_ALWAYS"):
        engine.add_grant(PermissionGrant(action="email.send", scope=ApprovalScope.SESSION))


def test_an_expired_grant_stops_working(engine):
    engine.add_grant(
        PermissionGrant(
            action="calendar.write",
            resource_glob="*",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    assert (
        engine.decide(ActionRequest(action="calendar.write", resource="x@y.z")).decision
        is PolicyDecision.ASK_ONCE
    )


def test_always_allow_always_gets_an_expiry(engine):
    grant = engine.add_grant(PermissionGrant(action="http.post", scope=ApprovalScope.ALWAYS))
    assert grant.expires_at is not None


# ------------------------------------------------------------------ autonomy (PART 97)


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (AutonomyLevel.SUGGEST_ONLY, PolicyDecision.ASK_ONCE),
        (AutonomyLevel.SAFE_ACTIONS, PolicyDecision.AUTO),
        (AutonomyLevel.MODERATE, PolicyDecision.AUTO),
        (AutonomyLevel.HIGH, PolicyDecision.AUTO),
    ],
)
def test_autonomy_gates_opening_an_application(engine, level, expected):
    engine.set_autonomy(level)
    assert engine.decide(ActionRequest(action="app.open")).decision is expected


def test_suggest_only_still_permits_reading(engine):
    engine.set_autonomy(AutonomyLevel.SUGGEST_ONLY)
    assert engine.decide(ActionRequest(action="file.read")).decision is PolicyDecision.AUTO


def test_safe_actions_gates_modification_but_not_opening(engine):
    engine.set_autonomy(AutonomyLevel.SAFE_ACTIONS)
    assert engine.decide(ActionRequest(action="app.open")).decision is PolicyDecision.AUTO
    assert engine.decide(ActionRequest(action="file.write")).decision is PolicyDecision.ASK_ONCE


def test_the_highest_autonomy_still_asks_for_the_dangerous_set(engine):
    """Autonomy can only tighten. The most permissive setting is still not admin."""
    engine.set_autonomy(AutonomyLevel.HIGH)
    for action in ("file.delete", "email.send", "shell.admin", "app.install"):
        assert engine.decide(ActionRequest(action=action)).decision is PolicyDecision.ASK_ALWAYS


# ------------------------------------------------------------------ privacy and lockdown


def test_secret_payloads_may_not_leave_the_machine(engine):
    verdict = engine.decide(
        ActionRequest(action="cloud.inference", sensitivity=DataSensitivity.SECRET)
    )
    assert verdict.decision is PolicyDecision.BLOCK
    assert verdict.rule == "privacy_secret"


def test_a_privacy_zone_blocks_the_surface_it_names():
    device_id = new_id()
    zones = PrivacyZoneRegistry(
        [PrivacyZone(name="bedroom", device_ids={device_id}, camera_disabled=True)]
    )
    engine = PermissionEngine(zones=zones)
    assert (
        engine.decide(ActionRequest(action="camera.capture", device_id=device_id)).decision
        is PolicyDecision.BLOCK
    )
    assert (
        engine.decide(ActionRequest(action="camera.capture", device_id=new_id())).decision
        is not PolicyDecision.BLOCK
    )


def test_lockdown_permits_only_reading(engine):
    engine.set_lockdown(True)
    assert engine.decide(ActionRequest(action="file.read")).decision is PolicyDecision.AUTO
    assert engine.decide(ActionRequest(action="app.open")).decision is PolicyDecision.BLOCK
    engine.set_lockdown(False)
    assert engine.decide(ActionRequest(action="app.open")).decision is PolicyDecision.AUTO
