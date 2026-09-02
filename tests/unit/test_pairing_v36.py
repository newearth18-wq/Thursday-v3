"""Secure device pairing (§80–83, Sprint 36).

The shared enrolment token authenticates *a* node, not *this* node. Everything in this
sprint exists to close that gap, and these tests are about the ways it could be left open:

* a device that pairs must stop being reachable through the shared token;
* a device that is revoked must stay revoked, including through the token;
* a code must authorise exactly one enrolment, briefly, and nothing else;
* nobody may register a key they do not hold, and nobody may enrol themselves.

The §174 acceptance criteria are the three named tests at the top of the last section:
expired pairing rejected, unknown device rejected, revoked device rejected.
"""

from __future__ import annotations

import json
import stat
from datetime import timedelta
from unittest import mock

import pytest
from thursday_security.credentials import FileCredentialStore
from thursday_security.device_auth import DeviceAuthenticator, sign, signing_payload
from thursday_security.keys import (
    PrivateKey,
    PublicKey,
    generate_keypair,
    hello_payload,
    pairing_payload,
)
from thursday_security.pairing import (
    CODE_DIGITS,
    MAX_ATTEMPTS,
    MAX_STARTS_PER_WINDOW,
    PairingError,
    PairingService,
    initial_trust,
)
from thursday_shared.enums import TrustLevel
from thursday_shared.ids import new_id
from thursday_shared.models import utcnow

TOKEN = "the-shared-enrolment-token"


def start_request(key: PrivateKey, *, name: str = "Office-PC", os: str = "Windows", **over):
    """A well-formed `pair/start`, signed by the key it offers.

    `caller` is kept out of the signed payload on purpose — it is the core's own view of who
    is asking, for rate limiting, and a caller-supplied value has no business being a claim.
    """
    caller = over.pop("caller", name)
    fields = {
        "public_key": key.public.encoded,
        "name": name,
        "os": os,
        "hostname": "office-pc.local",
        "nonce": "n-" + name,
        "issued_at": utcnow(),
    }
    fields.update(over)
    return {**fields, "signature": key.sign(pairing_payload(**fields)), "caller": caller}


def pair_device(service: PairingService, key: PrivateKey, **over):
    pending = service.start(**start_request(key, **over))
    return service.complete(pending.code)


# --------------------------------------------------------------------------- keys


def test_a_public_key_verifies_only_what_its_own_private_half_signed():
    private, public = generate_keypair()
    assert public.verify("hello", private.sign("hello"))
    assert not public.verify("hello!", private.sign("hello"))
    assert not public.verify("hello", generate_keypair()[0].sign("hello"))


def test_a_pairing_signature_is_not_a_valid_connection_signature():
    """Domain separation. Without the prefix, a captured pairing request would be a
    ready-made HELLO for the same device, and the two mean very different things."""
    private, public = generate_keypair()
    at = utcnow()
    pairing_sig = private.sign(
        pairing_payload(
            public_key=public.encoded,
            name="Office-PC",
            os="Windows",
            hostname="",
            nonce="n1",
            issued_at=at,
        )
    )
    connection = hello_payload(
        device_id=str(new_id()), name="Office-PC", os="Windows", nonce="n1", issued_at=at
    )
    assert not public.verify(connection, pairing_sig)


def test_a_private_key_round_trips_through_pem_and_keeps_its_identity():
    private, public = generate_keypair()
    reloaded = PrivateKey.from_pem(private.to_pem())
    assert public.verify("payload", reloaded.sign("payload"))
    assert reloaded.public.fingerprint == public.fingerprint


def test_a_fingerprint_is_stable_short_and_distinct():
    """The owner compares this against what the device printed, by eye."""
    private, public = generate_keypair()
    assert public.fingerprint == PublicKey(encoded=public.encoded).fingerprint
    assert len(public.fingerprint) == 19  # four groups of four, three colons
    assert public.fingerprint != generate_keypair()[1].fingerprint
    assert private.public.fingerprint == public.fingerprint


def test_verification_failures_are_indistinguishable():
    """A wrong key, a corrupt signature and rubbish all return the same False. Telling an
    unauthenticated caller which of their inputs was wrong helps only an attacker."""
    _, public = generate_keypair()
    assert public.verify("x", "not-base64-at-all") is False
    assert public.verify("x", "") is False
    assert public.verify("x", generate_keypair()[0].sign("x")) is False


# --------------------------------------------------------------------------- step one


def test_pairing_requires_proof_that_the_caller_holds_the_key_it_offers():
    """The whole point of `start`. Without it anyone could register any key under any name
    and the core would faithfully trust it for ever."""
    service = PairingService()
    impostor, _ = generate_keypair()
    _, offered = generate_keypair()

    request = start_request(impostor)
    request["public_key"] = offered.encoded  # signed by one key, offering another

    with pytest.raises(PairingError, match="not signed by the key"):
        service.start(**request)


def test_a_signature_cannot_be_lifted_onto_a_different_name():
    service = PairingService()
    key, _ = generate_keypair()
    request = start_request(key, name="Office-PC")
    request["name"] = "Home-Server"

    with pytest.raises(PairingError, match="not signed by the key"):
        service.start(**request)


def test_a_stale_pairing_request_is_refused():
    """A request timestamped far from now is a bad clock or a captured request being
    replayed, and neither should enrol a device."""
    service = PairingService()
    key, _ = generate_keypair()
    with pytest.raises(PairingError, match="too old or its clock"):
        service.start(**start_request(key, issued_at=utcnow() - timedelta(hours=1)))


def test_pairing_is_rate_limited_because_it_is_human_paced():
    service = PairingService()
    key, _ = generate_keypair()
    for i in range(MAX_STARTS_PER_WINDOW):
        service.start(**start_request(key, nonce=f"n{i}"))
    with pytest.raises(PairingError, match="too many pairing attempts"):
        service.start(**start_request(key, nonce="one-too-many"))


def test_the_code_is_readable_and_from_the_csprng():
    service = PairingService()
    key, _ = generate_keypair()
    codes = {
        service.start(**start_request(key, nonce=f"n{i}", caller=f"c{i}")).code for i in range(4)
    }
    assert len(codes) == 4
    for code in codes:
        assert len(code) == CODE_DIGITS
        assert code.isdigit()


def test_starting_a_pairing_registers_nothing_yet():
    """Proof of possession alone is not pairing: it would let any process that can reach the
    API enrol itself. Nothing is trusted until a person confirms."""
    service = PairingService()
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))
    assert service.credential(pending.device_id) is None
    assert service.known(pending.device_id) is False
    assert service.credentials() == []


# --------------------------------------------------------------------------- step two


def test_the_owner_confirming_the_code_registers_the_public_key():
    service = PairingService()
    key, public = generate_keypair()
    credential = pair_device(service, key)

    assert credential.public_key.encoded == public.encoded
    assert credential.fingerprint == public.fingerprint
    assert credential.active
    assert service.credential(credential.device_id) == credential


def test_a_code_enrols_exactly_one_device():
    service = PairingService()
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))
    service.complete(pending.code)

    with pytest.raises(PairingError, match=r"already been used|not valid"):
        service.complete(pending.code)


def test_an_expired_code_is_refused_and_a_wrong_one_is_indistinguishable_from_it():
    service = PairingService(code_ttl=timedelta(minutes=5))
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))

    with pytest.raises(PairingError, match="not valid"):
        service.complete(pending.code, now=utcnow() + timedelta(minutes=6))
    with pytest.raises(PairingError, match="not valid"):
        service.complete("000000")


def test_guessing_codes_is_what_gets_counted_not_guesses_at_a_code():
    """The failure this test exists for: a per-code attempt counter is never touched by an
    attacker, because the codes they guess do not exist. Six digits is a million
    combinations, and unlimited guesses make that a five-minute brute force."""
    service = PairingService(max_attempts=MAX_ATTEMPTS)
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))

    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(PairingError, match="not valid"):
            service.complete("000000")

    # Budget spent — and spent for the real code too, or the lockout would be trivially
    # sidestepped by the attacker who has now found it.
    with pytest.raises(PairingError, match="too many incorrect"):
        service.complete(pending.code)


def test_the_guess_budget_recovers_after_the_window():
    """A lockout that never lifts is an attacker permanently stopping the owner pairing.

    Note what the owner has to do: the ten-minute budget window outlives the five-minute
    code, so somebody spamming wrong codes costs them one restart of pairing. That is the
    right price — pairing is a deliberate act the owner is already standing at the machine
    for, and the alternative is a code that can be guessed without limit.
    """
    service = PairingService(rate_window=timedelta(minutes=10))
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))
    at = utcnow()

    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(PairingError):
            service.complete("000000", now=at)
    with pytest.raises(PairingError, match="too many incorrect"):
        service.complete(pending.code, now=at)

    later = at + timedelta(minutes=11)
    fresh = service.start(
        **start_request(key, nonce="after-lockout", caller="again", issued_at=later), now=later
    )
    assert service.complete(fresh.code, now=later).active


def test_a_used_code_counts_against_the_budget_too():
    """Replaying a code that worked once is a guess that happens to be right, and treating
    it as free would leave the cheapest guess uncounted."""
    service = PairingService()
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))
    service.complete(pending.code)

    with pytest.raises(PairingError):
        service.complete(pending.code)
    assert len(service._failures) == 1


def test_a_freshly_paired_device_is_limited_not_trusted():
    """Pairing a laptop and authorising it to drive the server are separate decisions
    (ADR 0024). §80 ends at "device becomes TRUSTED"; this stops one step short."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    assert initial_trust(credential) is TrustLevel.LIMITED
    assert initial_trust(credential) < TrustLevel.TRUSTED


# --------------------------------------------------------------------------- revocation


def test_revocation_keeps_the_record_and_deactivates_the_credential():
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)

    revoked = service.revoke(credential.device_id)
    assert revoked is not None and not revoked.active
    assert revoked.revoked_at is not None
    assert service.credential(credential.device_id) is None
    assert service.known(credential.device_id) is True  # sticky
    assert service.credentials() == []
    assert [c.device_id for c in service.credentials(include_revoked=True)] == [
        credential.device_id
    ]


def test_revoking_kills_a_pairing_the_device_has_in_flight():
    """Otherwise revocation is a race: revoke, and the device completes a pairing it started
    a minute earlier and is back."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)

    pending = service.start(**start_request(key, nonce="n2", caller="again"))
    pending.device_id = credential.device_id
    service._pending[pending.code] = pending

    service.revoke(credential.device_id)
    with pytest.raises(PairingError, match="not valid"):
        service.complete(pending.code)


def test_revoking_an_unknown_device_reports_it_rather_than_inventing_a_record():
    assert PairingService().revoke(new_id()) is None


# --------------------------------------------------------------------------- §174


def test_expired_pairing_is_rejected():
    """§174 acceptance. The code is not a credential: a leaked one costs one pairing inside
    its lifetime, and only because the lifetime is enforced."""
    service = PairingService(code_ttl=timedelta(minutes=5))
    key, _ = generate_keypair()
    pending = service.start(**start_request(key))

    with pytest.raises(PairingError):
        service.complete(pending.code, now=pending.expires_at + timedelta(seconds=1))
    assert service.credential(pending.device_id) is None


def test_an_unknown_device_is_rejected():
    """§174 acceptance. A device holding a perfectly good key that the core has never seen
    is not authenticated by it — it has to enrol first."""
    service = PairingService()
    key, _ = generate_keypair()
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    device_id = str(new_id())
    at = utcnow()
    outcome = auth.verify(
        device_id=device_id,
        name="Nobody-PC",
        os="Linux",
        nonce="n1",
        issued_at=at,
        signature=key.sign(
            hello_payload(
                device_id=device_id, name="Nobody-PC", os="Linux", nonce="n1", issued_at=at
            )
        ),
    )
    assert not outcome.ok


def test_a_revoked_device_is_rejected_even_though_it_still_holds_its_key():
    """§174 acceptance, and the reason revocation is worth having at all."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    def connect():
        at = utcnow()
        nonce = f"n-{at.timestamp()}"
        return auth.verify(
            device_id=str(credential.device_id),
            name=credential.name,
            os=credential.os,
            nonce=nonce,
            issued_at=at,
            signature=key.sign(
                hello_payload(
                    device_id=str(credential.device_id),
                    name=credential.name,
                    os=credential.os,
                    nonce=nonce,
                    issued_at=at,
                )
            ),
        )

    assert connect().ok
    service.revoke(credential.device_id)
    refused = connect()
    assert not refused.ok
    assert "revoked" in refused.reason


# --------------------------------------------------------------------------- the handover


def test_a_paired_device_can_no_longer_be_impersonated_with_the_shared_token():
    """The point of the whole sprint. If the token still worked for a paired device, anyone
    holding it could connect as the owner's PC and pairing would have bought nothing."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    at = utcnow()
    fields = {
        "device_id": str(credential.device_id),
        "name": credential.name,
        "os": credential.os,
        "nonce": "token-attempt",
        "issued_at": at,
    }
    outcome = auth.verify(**fields, signature=sign(TOKEN, signing_payload(**fields)))
    assert not outcome.ok
    assert "device's key" in outcome.reason


def test_a_device_that_has_not_paired_still_enrols_with_the_token():
    """Enrolment has to start somewhere (ADR 0013). The bootstrap path stays open for
    devices with no key on file, and only for them."""
    service = PairingService()
    auth = DeviceAuthenticator(TOKEN, pairing=service)
    at = utcnow()
    fields = {
        "device_id": str(new_id()),
        "name": "New-Laptop",
        "os": "Windows",
        "nonce": "first-boot",
        "issued_at": at,
    }
    assert auth.verify(**fields, signature=sign(TOKEN, signing_payload(**fields))).ok


def test_a_revoked_device_cannot_fall_back_to_the_shared_token():
    """Revocation a shared secret can route around is not revocation, and this is the exact
    hole the `known()`/`credential()` split exists to close."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    service.revoke(credential.device_id)
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    at = utcnow()
    fields = {
        "device_id": str(credential.device_id),
        "name": credential.name,
        "os": credential.os,
        "nonce": "back-in-please",
        "issued_at": at,
    }
    outcome = auth.verify(**fields, signature=sign(TOKEN, signing_payload(**fields)))
    assert not outcome.ok
    assert "revoked" in outcome.reason


def test_a_paired_device_still_cannot_replay_its_own_hello():
    """Key authentication replaces the token; it does not replace the replay defences."""
    service = PairingService()
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    at = utcnow()
    fields = {
        "device_id": str(credential.device_id),
        "name": credential.name,
        "os": credential.os,
        "nonce": "captured",
        "issued_at": at,
    }
    signature = key.sign(hello_payload(**fields))
    assert auth.verify(**fields, signature=signature).ok
    replay = auth.verify(**fields, signature=signature)
    assert not replay.ok
    assert "already been used" in replay.reason


def test_one_device_s_key_does_not_authenticate_another():
    service = PairingService()
    mine, _ = generate_keypair()
    theirs, _ = generate_keypair()
    ours = pair_device(service, mine)
    pair_device(service, theirs, name="Home-Server", nonce="n2", caller="server")
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    at = utcnow()
    fields = {
        "device_id": str(ours.device_id),
        "name": ours.name,
        "os": ours.os,
        "nonce": "wrong-key",
        "issued_at": at,
    }
    outcome = auth.verify(**fields, signature=theirs.sign(hello_payload(**fields)))
    assert not outcome.ok


# --------------------------------------------------------------------------- surviving a restart


def test_a_restart_does_not_lock_out_every_paired_device(tmp_path):
    """The failure this store exists to prevent, and it is the worst kind: the core forgets
    the key, the node signs with it anyway (correctly refusing the shared token), and every
    machine is locked out until somebody re-pairs it by hand."""
    path = tmp_path / "device_credentials.json"
    key, public = generate_keypair()
    credential = pair_device(PairingService(store=FileCredentialStore(path)), key)

    after_restart = PairingService(store=FileCredentialStore(path))
    restored = after_restart.credential(credential.device_id)
    assert restored is not None
    assert restored.public_key.encoded == public.encoded
    assert restored.fingerprint == public.fingerprint

    auth = DeviceAuthenticator(TOKEN, pairing=after_restart)
    at = utcnow()
    fields = {
        "device_id": str(credential.device_id),
        "name": credential.name,
        "os": credential.os,
        "nonce": "after-restart",
        "issued_at": at,
    }
    assert auth.verify(**fields, signature=key.sign(hello_payload(**fields))).ok


def test_a_restart_does_not_resurrect_a_revoked_device(tmp_path):
    """Silent, and visible only to whoever was revoked. Worth its own test."""
    path = tmp_path / "device_credentials.json"
    service = PairingService(store=FileCredentialStore(path))
    key, _ = generate_keypair()
    credential = pair_device(service, key)
    service.revoke(credential.device_id)

    after_restart = PairingService(store=FileCredentialStore(path))
    assert after_restart.credential(credential.device_id) is None
    assert after_restart.known(credential.device_id) is True

    auth = DeviceAuthenticator(TOKEN, pairing=after_restart)
    at = utcnow()
    fields = {
        "device_id": str(credential.device_id),
        "name": credential.name,
        "os": credential.os,
        "nonce": "back-again",
        "issued_at": at,
    }
    outcome = auth.verify(**fields, signature=key.sign(hello_payload(**fields)))
    assert not outcome.ok
    assert "revoked" in outcome.reason


def test_the_registry_holds_public_material_and_nothing_else(tmp_path):
    """§90: no secret in a plain file. What is here is public keys and names."""
    path = tmp_path / "device_credentials.json"
    key, _ = generate_keypair()
    pair_device(PairingService(store=FileCredentialStore(path)), key)

    body = path.read_text()
    assert "PRIVATE KEY" not in body
    assert key.to_pem() not in body
    assert TOKEN not in body
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_registry_that_cannot_be_read_does_not_take_the_core_down(tmp_path):
    """The safe direction: a dropped credential means one device re-pairs, and refusing to
    start means no device works at all."""
    path = tmp_path / "device_credentials.json"
    path.write_text("{ this is not json")
    assert PairingService(store=FileCredentialStore(path)).credentials() == []


def test_one_unreadable_record_does_not_discard_the_others(tmp_path):
    path = tmp_path / "device_credentials.json"
    key, _ = generate_keypair()
    good = pair_device(PairingService(store=FileCredentialStore(path)), key)

    rows = json.loads(path.read_text())
    rows.append({"device_id": "not-a-uuid", "public_key": "x"})
    path.write_text(json.dumps(rows))

    survivors = PairingService(store=FileCredentialStore(path)).credentials()
    assert [c.device_id for c in survivors] == [good.device_id]


def test_a_write_that_is_interrupted_cannot_truncate_the_registry(tmp_path):
    """Replaced, not written in place. A half-written registry locks out every device it
    lost, so the file is only ever swapped for a complete one."""
    path = tmp_path / "device_credentials.json"
    service = PairingService(store=FileCredentialStore(path))
    key, _ = generate_keypair()
    pair_device(service, key)
    before = path.read_text()

    second, _ = generate_keypair()
    with (
        mock.patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        pair_device(service, second, name="Second", nonce="n2", caller="second")

    assert path.read_text() == before
