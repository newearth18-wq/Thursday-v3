"""Permission engine, policy table and grants (§36–38)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.permissions import PermissionEngine
from thursday_security.policy import HARD_BLOCKED, PolicyTable
from thursday_security.privacy import PrivacyZone, PrivacyZoneRegistry
from thursday_shared.enums import (
    ApprovalScope,
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


def test_ordinary_reversible_work_is_automatic(engine):
    verdict = engine.decide(ActionRequest(action="open_app", resource="chrome"))
    assert verdict.decision is PolicyDecision.AUTO
    assert verdict.rule == "policy_default_auto"


def test_outward_facing_actions_require_approval(engine):
    for action in ("send_email", "send_message", "publish", "purchase"):
        assert engine.decide(ActionRequest(action=action)).decision is PolicyDecision.ASK


def test_unknown_actions_fail_closed(engine):
    """An action nobody declared is ASK, never AUTO."""
    assert (
        engine.decide(ActionRequest(action="frobnicate_the_thing")).decision is PolicyDecision.ASK
    )


def test_hard_blocked_actions_have_no_override_path(engine):
    for action in ("disable_antivirus", "modify_audit_log", "grant_self_admin"):
        assert action in HARD_BLOCKED
        assert engine.decide(ActionRequest(action=action)).decision is PolicyDecision.BLOCK

    with pytest.raises(PermissionError):
        engine.policy.override("disable_antivirus", PolicyDecision.AUTO)
    with pytest.raises(PermissionError):
        engine.add_grant(PermissionGrant(action="disable_antivirus"))
    assert engine.decide(ActionRequest(action="disable_antivirus")).decision is PolicyDecision.BLOCK


def test_a_user_may_loosen_low_levels_but_not_system_ones():
    policy = PolicyTable()
    policy.override("delete", PolicyDecision.AUTO)
    assert policy.get("delete").default is PolicyDecision.AUTO

    policy.override("elevate", PolicyDecision.AUTO)
    assert policy.get("elevate").default is PolicyDecision.ASK


def test_blast_radius_turns_an_automatic_action_into_a_question(engine):
    assert (
        engine.decide(ActionRequest(action="move", object_count=3)).decision is PolicyDecision.AUTO
    )
    verdict = engine.decide(ActionRequest(action="move", object_count=342))
    assert verdict.decision is PolicyDecision.ASK
    assert verdict.rule == "blast_radius"


def test_irreversible_actions_are_asked_about(engine):
    verdict = engine.decide(ActionRequest(action="copy", reversible=False))
    assert verdict.decision is PolicyDecision.ASK
    assert verdict.rule == "irreversible"


def test_permissions_intersect_and_never_widen():
    wide = PermissionSet(max_level=PermissionLevel.ADMIN, allowed_tools=["a", "b", "c"])
    narrow = PermissionSet(max_level=PermissionLevel.READ, allowed_tools=["b", "z"])
    combined = wide.intersect(narrow)
    assert combined.max_level is PermissionLevel.READ
    assert combined.allowed_tools == ["b"]


def test_an_agent_cannot_exceed_its_ceiling(engine):
    verdict = engine.decide(
        ActionRequest(action="write_file", resource="/home/u/a.txt"),
        permissions=PermissionSet(max_level=PermissionLevel.READ),
    )
    assert verdict.decision is PolicyDecision.BLOCK
    assert verdict.rule == "permission_ceiling"


def test_path_scopes_confine_an_agent(engine):
    permissions = PermissionSet(max_level=PermissionLevel.MODIFY, path_scopes=["/home/u/work/*"])
    assert (
        engine.decide(
            ActionRequest(action="write_file", resource="/home/u/work/report.md"),
            permissions=permissions,
        ).decision
        is PolicyDecision.AUTO
    )
    assert (
        engine.decide(
            ActionRequest(action="write_file", resource="/etc/passwd"), permissions=permissions
        ).decision
        is PolicyDecision.BLOCK
    )


def test_grants_are_scoped_and_expire(engine):
    engine.add_grant(
        PermissionGrant(
            action="send_email", resource_glob="*@school.ac.th", scope=ApprovalScope.ALWAYS
        )
    )
    assert (
        engine.decide(ActionRequest(action="send_email", resource="dean@school.ac.th")).decision
        is PolicyDecision.AUTO
    )
    assert (
        engine.decide(ActionRequest(action="send_email", resource="someone@elsewhere.com")).decision
        is PolicyDecision.ASK
    )


def test_an_expired_grant_stops_working(engine):
    engine.add_grant(
        PermissionGrant(
            action="send_email",
            resource_glob="*",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    assert (
        engine.decide(ActionRequest(action="send_email", resource="x@y.z")).decision
        is PolicyDecision.ASK
    )


def test_always_allow_always_gets_an_expiry(engine):
    grant = engine.add_grant(PermissionGrant(action="open_url", scope=ApprovalScope.ALWAYS))
    assert grant.expires_at is not None


def test_secret_payloads_may_not_leave_the_machine(engine):
    verdict = engine.decide(
        ActionRequest(action="cloud_inference", sensitivity=DataSensitivity.SECRET)
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
        engine.decide(ActionRequest(action="camera_capture", device_id=device_id)).decision
        is PolicyDecision.BLOCK
    )
    assert (
        engine.decide(ActionRequest(action="camera_capture", device_id=new_id())).decision
        is not PolicyDecision.BLOCK
    )


def test_lockdown_permits_only_reading(engine):
    engine.set_lockdown(True)
    assert engine.decide(ActionRequest(action="read_file")).decision is PolicyDecision.AUTO
    assert engine.decide(ActionRequest(action="open_app")).decision is PolicyDecision.BLOCK
    engine.set_lockdown(False)
    assert engine.decide(ActionRequest(action="open_app")).decision is PolicyDecision.AUTO


def test_high_risk_is_asked_about_even_at_a_low_level(engine):
    verdict = engine.decide(
        ActionRequest(action="open_app", risk=RiskLevel.HIGH, level=PermissionLevel.OPEN)
    )
    assert verdict.decision is PolicyDecision.ASK
