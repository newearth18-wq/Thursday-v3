"""Getting back in (BIOMETRIC IDENTITY §88) — Sprint 78.

Two failures pull in opposite directions and both are real.

§45 forbids the soft one: *"ห้ามให้ LLM ตัดสินว่า 'ดูเหมือนเป็นเจ้าของ ให้เข้าได้'."* Recovery
is where every biometric defence is deliberately set aside, so it is the part an attacker aims
at — and a model asked "does this seem like the owner?" will sometimes say yes to somebody
persuasive, which is exactly what an attacker brings.

§46 and §47 forbid the hard one: the owner is ill and cannot speak, the microphone broke, the
room is dark, the camera is dead. A security system that locks the owner out of their own
machine has not been secure; it has been useless in a way that gets it uninstalled.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from thursday_security import recovery_identity
from thursday_security.identity import AuthLevel, Factor
from thursday_security.recovery_identity import (
    COOLDOWN,
    MAX_ATTEMPTS,
    RECOVERY_FAILED,
    RecoveryService,
    owner_alert,
)

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest.fixture
def service() -> RecoveryService:
    return RecoveryService()


# ============================================================ §45 nothing here reasons


def test_the_recovery_module_imports_nothing_that_could_form_an_opinion():
    """§45, structurally. Every path is a comparison against something stored, and the way to
    keep that true is for there to be no model to ask."""
    tree = ast.parse(Path(recovery_identity.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    for reasoning in ("thursday_models", "thursday_agents", "openai", "anthropic"):
        assert reasoning not in imported


def test_no_recovery_path_takes_a_free_text_justification():
    """The shape §45 forbids is a method that could be persuaded. None of these accepts
    prose, so there is nothing to persuade with."""
    for name in ("with_pin", "with_recovery_key", "with_os_biometric"):
        parameters = set(inspect.signature(getattr(RecoveryService, name)).parameters)
        for persuasion in ("reason", "explanation", "context", "message", "why", "prompt"):
            assert persuasion not in parameters


def test_a_pin_is_compared_in_constant_time():
    """A comparison that returns early on the first wrong character leaks the PIN one
    character at a time to anybody who can measure it."""
    source = Path(recovery_identity.__file__).read_text()
    assert "hmac.compare_digest" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(
            isinstance(op, ast.Eq | ast.NotEq) for op in node.ops
        ):
            rendered = ast.unparse(node)
            assert "pin" not in rendered.lower(), f"PIN compared with ==: {rendered}"


def test_the_pin_itself_is_never_stored(service):
    """Salted and stretched. A store that holds the PIN is one that leaks it."""
    service.set_pin("192837")
    blob = repr(service.__dict__).encode()
    assert b"192837" not in blob


# ============================================================== §40 refusals say nothing


def test_every_failure_says_the_same_thing(service):
    """An attacker who can tell "wrong PIN" from "no PIN set" from "locked out" is an
    attacker being handed a map."""
    messages = set()

    messages.add(service.with_pin("0000", now=NOW).message)  # nothing configured
    service.set_pin("1234")
    messages.add(service.with_pin("9999", now=NOW).message)  # wrong
    messages.add(service.with_recovery_key("nope", now=NOW).message)  # no key configured

    for i in range(MAX_ATTEMPTS + 2):
        messages.add(service.with_pin("9999", now=NOW + timedelta(seconds=i)).message)

    assert messages == {RECOVERY_FAILED}


def test_a_failure_never_says_how_close_it_was(service):
    service.set_pin("1234")
    outcome = service.with_pin("1235", now=NOW)
    assert outcome.ok is False
    assert not any(ch.isdigit() for ch in outcome.message)
    assert "%" not in outcome.message


def test_the_reason_is_kept_for_the_audit_trail_but_not_returned_to_the_caller(service):
    """The distinction that makes both possible: an operator needs to know what happened and
    the person failing must not."""
    service.set_pin("1234")
    outcome = service.with_pin("9999", now=NOW)
    assert outcome.reason
    assert outcome.reason != outcome.message


def test_no_pin_configured_is_indistinguishable_from_a_wrong_one(service):
    """Counted as an attempt even with nothing to compare against, so the two cases cannot
    be told apart by timing out the rate limit either."""
    for i in range(MAX_ATTEMPTS):
        service.with_pin("0000", now=NOW + timedelta(seconds=i))
    assert service.locked_out is True


# ========================================================== §40 rate limiting, both ways


def test_repeated_failures_start_a_cooldown(service):
    service.set_pin("1234")
    for i in range(MAX_ATTEMPTS):
        service.with_pin("9999", now=NOW + timedelta(seconds=i))
    assert service.locked_out is True

    # And the correct PIN does not work during it, or the cooldown means nothing.
    assert service.with_pin("1234", now=NOW + timedelta(seconds=MAX_ATTEMPTS)).ok is False


def test_the_cooldown_ends(service):
    """§44: the owner must be able to get back in eventually. A cooldown that never lifts is
    a lockout, and a lockout is how a security system gets uninstalled."""
    service.set_pin("1234")
    for i in range(MAX_ATTEMPTS):
        service.with_pin("9999", now=NOW + timedelta(seconds=i))

    later = NOW + COOLDOWN + timedelta(minutes=1)
    assert service.with_pin("1234", now=later).ok is True


def test_failures_spread_over_time_do_not_accumulate(service):
    """Without a window, five wrong PINs across a year lock the owner out on the sixth."""
    service.set_pin("1234")
    for day in range(MAX_ATTEMPTS * 2):
        service.with_pin("9999", now=NOW + timedelta(days=day))
    assert service.locked_out is False


def test_a_success_clears_the_count(service):
    service.set_pin("1234")
    for i in range(MAX_ATTEMPTS - 1):
        service.with_pin("9999", now=NOW + timedelta(seconds=i))
    assert service.with_pin("1234", now=NOW + timedelta(seconds=10)).ok is True
    assert service.failed_attempts() == 0


# ================================================================ §44 the fallbacks work


def test_a_correct_pin_recovers_a_session(service):
    service.set_pin("1234")
    outcome = service.with_pin("1234", now=NOW)
    assert outcome.ok is True
    assert outcome.factor is Factor.PIN


def test_a_pin_recovers_one_factor_rather_than_what_was_lost(service):
    """A PIN proves somebody knows a number. It is knowledge, not identity, and restoring a
    STRONG session from one would make the PIN the weakest link with the highest privilege."""
    service.set_pin("1234")
    assert service.with_pin("1234", now=NOW).level is AuthLevel.SINGLE


def test_a_recovery_key_works_once_and_only_once(service):
    """A key that survives its own use is a permanent bypass sitting on whatever the owner
    wrote it down on."""
    key = service.issue_recovery_key()
    assert service.with_recovery_key(key, now=NOW).ok is True
    assert service.with_recovery_key(key, now=NOW + timedelta(seconds=1)).ok is False


def test_the_recovery_key_is_not_stored_in_a_readable_form(service):
    key = service.issue_recovery_key()
    assert key.encode() not in repr(service.__dict__).encode()


def test_an_os_verified_biometric_recovers_more_than_a_pin(service):
    """§37. The OS did the work and never handed Thursday the raw biometric, which is a much
    better bet than a stub matcher — stated rather than hidden."""
    outcome = service.with_os_biometric(verified=True, now=NOW)
    assert outcome.ok is True
    assert outcome.level > AuthLevel.SINGLE


def test_the_os_saying_no_is_a_failure_like_any_other(service):
    outcome = service.with_os_biometric(verified=False, now=NOW)
    assert outcome.ok is False
    assert outcome.message == RECOVERY_FAILED


# ============================================== §44/§46/§47 the owner cannot be stranded


def test_a_fresh_service_has_no_way_back_in_and_says_so(service):
    """What setup checks before enabling biometrics. Turning on face recognition with no
    fallback is how somebody is locked out of their own machine by a broken webcam."""
    assert service.configured is False


@pytest.mark.parametrize("method", ["pin", "key"])
def test_configuring_either_fallback_is_enough(service, method):
    if method == "pin":
        service.set_pin("1234")
    else:
        service.issue_recovery_key()
    assert service.configured is True


def test_a_silent_owner_can_still_get_in(service):
    """§46: ill, hoarse, cannot speak, microphone broken. Every path here is silent."""
    service.set_pin("1234")
    assert service.with_pin("1234", now=NOW).ok is True


def test_a_dark_room_does_not_matter_either(service):
    """§47: face recognition cannot work, so the fallbacks must not depend on a camera."""
    service.set_pin("1234")
    assert service.with_pin("1234", now=NOW).ok is True
    assert service.with_os_biometric(verified=True, now=NOW).ok is True


def test_a_short_pin_is_refused_at_the_point_of_setting_it(service):
    """Refused where it can be fixed, rather than accepted and weak forever."""
    with pytest.raises(ValueError):
        service.set_pin("1")


# ==================================================================== §41 the owner alert


def test_the_alert_tells_the_owner_what_happened_without_describing_anybody():
    """§41: no image of an unknown person by default. Naming what somebody looked like turns
    a security notice into surveillance of whoever walked past.

    Checked over the *values* with word boundaries. The first version searched the whole
    stringified dict for "age" and matched the key `image` — a substring false positive, and
    a reminder that a scan looking for words should be looking for words.
    """
    import re

    alert = owner_alert(device="Office-PC", at=NOW, attempts=3)
    assert alert["image"] is None

    described = " ".join(str(v) for v in alert.values()).lower()
    for description in ("face", "photo", "male", "female", "age", "looked", "person"):
        assert not re.search(rf"\b{description}\b", described), description


def test_the_alert_names_the_device_and_the_time():
    alert = owner_alert(device="Office-PC", at=NOW, attempts=3)
    assert alert["device"] == "Office-PC"
    assert alert["at"] == NOW.isoformat()
    assert alert["attempts"] == 3
