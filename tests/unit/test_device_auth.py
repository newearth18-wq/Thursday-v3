"""Device authentication (§9.1, ADR 0013).

The node is what runs commands on the owner's real machine and reports whether they
worked. Both halves are attack surface, and the second is the worse one: an impostor node
that reports ``verified: true`` for something it never did defeats the property every
other guarantee in this system is built on.

So these tests are about one question — can anything that does not hold the token get
itself treated as the owner's PC?
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from thursday_security.device_auth import (
    MAX_CLOCK_SKEW,
    DeviceAuthenticator,
    sign,
    signing_payload,
)
from thursday_shared.models import utcnow

TOKEN = "a-real-enrolment-token"


def hello(**overrides):
    frame = {
        "device_id": "11111111-1111-1111-1111-111111111111",
        "name": "Office-PC",
        "os": "Windows",
        "nonce": "abc123",
        "issued_at": utcnow(),
    }
    frame.update(overrides)
    return frame


def signed(token: str = TOKEN, **overrides) -> dict:
    frame = hello(**overrides)
    return {**frame, "signature": sign(token, signing_payload(**frame))}


def test_a_node_holding_the_token_is_accepted():
    assert DeviceAuthenticator(TOKEN).verify(**signed())


def test_a_node_with_the_wrong_token_is_rejected():
    outcome = DeviceAuthenticator(TOKEN).verify(**signed(token="guessed-it"))
    assert not outcome.ok
    assert "did not match" in outcome.reason


def test_an_unsigned_hello_is_rejected():
    outcome = DeviceAuthenticator(TOKEN).verify(**hello(), signature="")
    assert not outcome.ok


def test_a_core_with_no_token_configured_refuses_everything():
    """Fail closed. Reading "signatures required, none configured" as "allow all" is how
    an unauthenticated device ends up trusted in production."""
    outcome = DeviceAuthenticator(None).verify(**signed())
    assert not outcome.ok
    assert "no device token" in outcome.reason


@pytest.mark.parametrize("field", ["device_id", "name", "os"])
def test_the_claim_cannot_be_altered_after_signing(field):
    """Signing only the nonce would let a captured HELLO be re-presented under another
    name — the attacker's machine claiming to be the owner's."""
    frame = signed()
    frame[field] = "something-else"
    assert not DeviceAuthenticator(TOKEN).verify(**frame)


def test_a_captured_hello_cannot_be_replayed():
    auth = DeviceAuthenticator(TOKEN)
    frame = signed()
    assert auth.verify(**frame)
    replay = auth.verify(**frame)
    assert not replay.ok
    assert "already been used" in replay.reason


def test_a_stale_hello_is_refused_even_with_a_fresh_nonce():
    old = utcnow() - MAX_CLOCK_SKEW - timedelta(seconds=30)
    outcome = DeviceAuthenticator(TOKEN).verify(**signed(issued_at=old))
    assert not outcome.ok
    assert "clock" in outcome.reason


def test_modest_clock_drift_is_tolerated():
    """A laptop that woke from sleep with a drifted clock is not an attacker."""
    drifted = utcnow() - MAX_CLOCK_SKEW + timedelta(seconds=30)
    assert DeviceAuthenticator(TOKEN).verify(**signed(issued_at=drifted))


def test_the_nonce_memory_is_bounded():
    """A node reconnecting in a loop must not be able to grow the core's memory."""
    from thursday_security.device_auth import MAX_REMEMBERED_NONCES

    auth = DeviceAuthenticator(TOKEN)
    for index in range(MAX_REMEMBERED_NONCES + 200):
        assert auth.verify(**signed(nonce=f"n{index}"))
    assert len(auth._seen) <= MAX_REMEMBERED_NONCES


def test_checking_can_be_switched_off_only_deliberately():
    """The loopback node inside the CLI has no socket to authenticate over."""
    assert DeviceAuthenticator(None, required=False).verify(**hello(), signature="")
