"""Rotating the shared enrolment token (§117, V17).

Device keys have rotated since Sprint 52 and this one has not, because the obvious rotation
is a lie: change the variable, restart, and every node that has not yet paired is refused
with no way to find out what still held the old secret.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_security.device_auth import DeviceAuthenticator, sign, signing_payload
from thursday_security.enrolment import (
    MIN_OVERLAP,
    Rotation,
    RotationRefused,
    begin,
    fingerprint,
)

OLD = "the-token-every-node-was-given"
NEW = "the-token-they-should-be-given-now"
NOON = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def rotation(overlap: timedelta = timedelta(days=7)) -> Rotation:
    return begin(OLD, NEW, overlap=overlap, now=NOON)


def hello(**overrides):
    frame = {
        "device_id": "11111111-1111-1111-1111-111111111111",
        "name": "Office-PC",
        "os": "Windows",
        "nonce": "abc123",
        "issued_at": NOON,
    }
    frame.update(overrides)
    return frame


def signed(token: str, **overrides) -> dict:
    frame = hello(**overrides)
    return {**frame, "signature": sign(token, signing_payload(**frame))}


# ------------------------------------------------------------------- the overlap window


def test_both_tokens_work_during_the_overlap():
    """The whole reason the window exists: a node mid-enrolment is not cut off by the
    owner deciding to rotate."""
    auth = DeviceAuthenticator(None, rotation=rotation())
    assert auth.verify(**signed(NEW), now=NOON)
    assert auth.verify(**signed(OLD, nonce="def456"), now=NOON)


def test_the_old_token_stops_working_when_the_window_closes():
    auth = DeviceAuthenticator(None, rotation=rotation())
    after = NOON + timedelta(days=8)
    assert auth.verify(**signed(NEW, issued_at=after), now=after)
    assert not auth.verify(**signed(OLD, issued_at=after, nonce="def456"), now=after)


def test_a_node_that_missed_the_window_is_told_what_happened():
    """ "The signature did not match" sends the owner looking for a typo. The date the old
    token stopped working sends them to the answer."""
    auth = DeviceAuthenticator(None, rotation=rotation())
    after = NOON + timedelta(days=8)
    outcome = auth.verify(**signed(OLD, issued_at=after), now=after)
    assert not outcome.ok
    assert "2026-09-19" in outcome.reason


def test_a_wrong_token_during_the_window_is_still_refused():
    auth = DeviceAuthenticator(None, rotation=rotation())
    assert not auth.verify(**signed("guessed-it"), now=NOON)


# ------------------------------------------------- knowing whether the rotation is over


def test_an_acceptance_says_which_token_let_the_node_in():
    """ "Is anything still using the old secret?" becomes a question the logs answer,
    rather than one the owner settles by rotating and seeing what breaks."""
    auth = DeviceAuthenticator(None, rotation=rotation())
    on_new = auth.verify(**signed(NEW), now=NOON)
    on_old = auth.verify(**signed(OLD, nonce="def456"), now=NOON)

    assert fingerprint(NEW) in on_new.reason and "ปัจจุบัน" in on_new.reason
    assert fingerprint(OLD) in on_old.reason and "กำลังเลิกใช้" in on_old.reason


def test_a_fingerprint_identifies_a_token_without_revealing_it():
    assert fingerprint(OLD) != fingerprint(NEW)
    assert fingerprint(OLD) == fingerprint(OLD), "stable across calls"
    assert OLD[:4] not in fingerprint(OLD)
    assert len(fingerprint(OLD)) == 8


def test_what_the_owner_may_see_never_includes_a_secret():
    shown = rotation().to_dict()
    assert OLD not in str(shown) and NEW not in str(shown)
    assert shown["rotating"] is True
    assert shown["current"] == fingerprint(NEW)
    assert shown["retiring"] == fingerprint(OLD)


# ------------------------------------------------------------------------- refusals


def test_rotating_a_token_to_itself_is_refused():
    """What this looks like in practice is somebody setting the new variable to the old
    value: a rotation reported for ever that changed nothing."""
    with pytest.raises(RotationRefused, match="เหมือนกัน"):
        begin(OLD, OLD, overlap=timedelta(days=1))


def test_an_overlap_too_short_to_use_is_refused():
    """An overlap measured in seconds is the un-rotated behaviour with extra steps."""
    with pytest.raises(RotationRefused, match="สั้นเกินไป"):
        begin(OLD, NEW, overlap=MIN_OVERLAP / 2)


def test_a_retiring_token_with_no_end_date_is_refused():
    """A second accepted secret that never expires is not a rotation — it is two tokens."""
    with pytest.raises(RotationRefused, match="วันเลิกใช้"):
        Rotation(current=NEW, retiring=OLD)


def test_an_end_date_with_nothing_retiring_is_refused():
    with pytest.raises(RotationRefused, match="ไม่มี token"):
        Rotation(current=NEW, retires_at=NOON)


def test_there_is_no_token_generator_anywhere_in_the_module():
    """A secret the machine invented and wrote down is a secret with one more copy of
    itself in the world, in a place the owner does not know about."""
    import inspect

    from thursday_security import enrolment

    source = inspect.getsource(enrolment)
    assert "secrets." not in source
    assert "token_urlsafe" not in source
    assert "token_hex" not in source


# ----------------------------------------------------------- nothing else is disturbed


def test_a_single_token_still_works_exactly_as_before():
    """Every existing caller passes one token positionally. A rotation is the same thing
    with a window, not a different API."""
    auth = DeviceAuthenticator(OLD)
    assert auth.configured
    assert auth.verify(**signed(OLD), now=NOON)
    assert not auth.verify(**signed(NEW, nonce="def456"), now=NOON)


def test_no_token_at_all_still_fails_closed():
    auth = DeviceAuthenticator(None)
    assert not auth.configured
    outcome = auth.verify(**signed(OLD), now=NOON)
    assert not outcome.ok and "no device token" in outcome.reason


def test_a_rotation_does_not_reopen_the_token_path_for_a_paired_device(monkeypatch):
    """Pairing closes the shared token for that device permanently. A second live token
    during a rotation must not be a second way in for a machine that already has a key."""
    from uuid import UUID

    identifier = UUID("11111111-1111-1111-1111-111111111111")

    class Key:
        def verify(self, payload: str, signature: str) -> bool:
            return False

    class Credential:
        public_key = Key()

    class Pairing:
        def known(self, device_id):
            return device_id == identifier

        def credential(self, device_id):
            return Credential()

    auth = DeviceAuthenticator(None, rotation=rotation(), pairing=Pairing())
    for token in (NEW, OLD):
        outcome = auth.verify(**signed(token, nonce=f"n-{token[:3]}"), now=NOON)
        assert not outcome.ok
        assert "device's key" in outcome.reason


def test_the_replay_defence_still_applies_across_both_tokens():
    """A nonce spent on one token must not be spendable again on the other."""
    auth = DeviceAuthenticator(None, rotation=rotation())
    assert auth.verify(**signed(NEW, nonce="once"), now=NOON)
    assert not auth.verify(**signed(OLD, nonce="once"), now=NOON)
