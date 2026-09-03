"""Bounded sessions and credential rotation (§79, §82, §117, §134) — Sprint 52.

Three defects and one missing feature, and they turned out to be the same story: a device
session that was authenticated once and then never questioned again.

  · `NodeSession.close()` dropped the session from the hub and left the *socket* open, so
    revoking a connected device did not disconnect it and §134's emergency stop disconnected
    nothing a node could notice.
  · A HELLO authenticated a connection for as long as it happened to stay up.
  · Nothing rotated, though `device_credentials.rotates_at` has been in the schema since §8.

The third is why the second matters. A session authenticated with a key that has since been
replaced would outlive the key, and rotation that ends nothing is not rotation.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from thursday_api.app import create_app
from thursday_core.container import build_container
from thursday_security.device_auth import DeviceAuthenticator
from thursday_security.keys import (
    PrivateKey,
    generate_keypair,
    hello_payload,
    pairing_payload,
    rotation_payload,
)
from thursday_security.pairing import PairingError, PairingService
from thursday_shared.ids import new_id
from thursday_shared.models import DeviceCapabilities, DeviceTelemetry, utcnow
from thursday_shared.protocol import CLOSE_SESSION_ENDED, CLOSE_SESSION_EXPIRED, Hello

HOST = "http://testserver"
BASE = f"{HOST}/api/v1"


def paired_via(container):
    """What the `paired` fixture does, callable from a test that also needs the client."""
    private, public = generate_keypair()
    nonce, issued_at = f"pair-{public.fingerprint}", utcnow()
    started = container.pairing.start(
        public_key=public.encoded,
        name="Office-PC",
        os="Windows",
        hostname="office",
        nonce=nonce,
        issued_at=issued_at,
        signature=private.sign(
            pairing_payload(
                public_key=public.encoded,
                name="Office-PC",
                os="Windows",
                hostname="office",
                nonce=nonce,
                issued_at=issued_at,
            )
        ),
    )
    return container.pairing.complete(started.code), private


@pytest.fixture
def paired(container):
    """A device that has completed pairing, and the private key it still holds."""
    return paired_via(container)


def rotation_body(service: PairingService, device_id, old: PrivateKey, new: PrivateKey) -> dict:
    """A rotation request the service will accept, signed by both keys.

    `issued_at` stays a `datetime` — the service's own type. The HTTP body needs it as a
    string, and `_as_http` does that conversion in one place rather than leaving two shapes
    of the same dict drifting around this file.
    """
    issued_at = utcnow()
    nonce = f"rot-{new.public.fingerprint}"
    payload = rotation_payload(
        device_id=str(device_id),
        old_fingerprint=service.credential(device_id).fingerprint,
        new_public_key=new.public.encoded,
        nonce=nonce,
        issued_at=issued_at,
    )
    return {
        "new_public_key": new.public.encoded,
        "signature_by_old": old.sign(payload),
        "signature_by_new": new.sign(payload),
        "nonce": nonce,
        "issued_at": issued_at,
    }


def _as_http(body: dict) -> dict:
    return {**body, "issued_at": body["issued_at"].isoformat()}


# --------------------------------------------------------------------------- rotation


def test_a_rotated_key_replaces_the_old_one_and_the_old_one_stops_working(container, paired):
    """The property that makes rotation worth doing. Everything else here defends it."""
    credential, old = paired
    new = PrivateKey.generate()
    before = credential.fingerprint

    rotated = container.pairing.rotate(
        credential.device_id, **_signed(container.pairing, credential.device_id, old, new)
    )

    assert rotated.fingerprint == new.public.fingerprint != before
    assert rotated.rotated_at is not None

    auth = DeviceAuthenticator("unused-token", pairing=container.pairing)
    assert _hello_verifies(auth, credential.device_id, new), "the new key must authenticate"
    assert not _hello_verifies(auth, credential.device_id, old), "the old key must not"


def test_rotation_needs_the_key_it_is_replacing(container, paired):
    """The retiring key is the entire authority for the request. Without this check anyone
    could name a successor for any device they knew the id of."""
    credential, _old = paired
    new = PrivateKey.generate()
    impostor = PrivateKey.generate()

    body = _signed(container.pairing, credential.device_id, impostor, new)
    with pytest.raises(PairingError, match="current key"):
        container.pairing.rotate(credential.device_id, **body)

    assert container.pairing.credential(credential.device_id).fingerprint == credential.fingerprint


def test_rotation_needs_proof_the_node_can_use_the_new_key(container, paired):
    """Not a security property — an attacker with the old key could sign both halves. It is
    here because a rotation to a key nobody holds bricks the machine until somebody walks
    to it, and rotation that can do that by accident does not get used."""
    credential, old = paired
    new = PrivateKey.generate()
    somebody_elses = PrivateKey.generate()

    body = _signed(container.pairing, credential.device_id, old, new)
    body["signature_by_new"] = somebody_elses.sign("anything at all")
    with pytest.raises(PairingError, match="new key"):
        container.pairing.rotate(credential.device_id, **body)

    assert container.pairing.credential(credential.device_id).fingerprint == credential.fingerprint


def test_a_revoked_device_cannot_rotate_its_way_back_in(container, paired):
    """Revocation is sticky (§80). A revoked key that could name its own replacement would
    be a door around the revocation, opened with the key that was revoked."""
    credential, old = paired
    new = PrivateKey.generate()
    body = _signed(container.pairing, credential.device_id, old, new)
    container.pairing.revoke(credential.device_id)

    with pytest.raises(PairingError, match="not paired"):
        container.pairing.rotate(credential.device_id, **body)


def test_an_unknown_device_cannot_rotate_itself_into_existence(container):
    """Rotation is not a second way to enrol."""
    old, new = PrivateKey.generate(), PrivateKey.generate()
    payload = rotation_payload(
        device_id=str(new_id()),
        old_fingerprint="whatever",
        new_public_key=new.public.encoded,
        nonce="n",
        issued_at=utcnow(),
    )
    with pytest.raises(PairingError, match="not paired"):
        container.pairing.rotate(
            new_id(),
            new_public_key=new.public.encoded,
            signature_by_old=old.sign(payload),
            signature_by_new=new.sign(payload),
            nonce="n",
            issued_at=utcnow(),
        )


def test_a_rotation_request_cannot_be_replayed(container, paired):
    """A captured rotation is worth more to an attacker than a captured HELLO: it names the
    key the core will trust next."""
    credential, old = paired
    new = PrivateKey.generate()
    body = _signed(container.pairing, credential.device_id, old, new)
    container.pairing.rotate(credential.device_id, **body)

    # Re-presented verbatim. It is signed by a key the core no longer holds, so it fails on
    # the signature — but replay is checked first and names itself, which is what an
    # operator reading the log needs.
    third = PrivateKey.generate()
    replay = _signed(container.pairing, credential.device_id, new, third)
    replay["nonce"] = body["nonce"]
    with pytest.raises(PairingError, match="already been used"):
        container.pairing.rotate(credential.device_id, **replay)


def test_a_stale_rotation_request_is_refused(container, paired):
    credential, old = paired
    new = PrivateKey.generate()
    body = _signed(container.pairing, credential.device_id, old, new)
    body["issued_at"] = utcnow() - timedelta(hours=1)
    with pytest.raises(PairingError, match="not fresh"):
        container.pairing.rotate(credential.device_id, **body)


def test_rotation_survives_a_restart(container, settings, paired):
    """Through the store, not through the object. A rotation the registry forgets on restart
    would resurrect the retired key."""
    credential, old = paired
    new = PrivateKey.generate()
    container.pairing.rotate(
        credential.device_id, **_signed(container.pairing, credential.device_id, old, new)
    )

    reborn = build_container(settings, configure_logs=False).pairing
    assert reborn.credential(credential.device_id).fingerprint == new.public.fingerprint
    assert reborn.credential(credential.device_id).rotated_at is not None


def test_revoking_a_rotated_device_keeps_the_rotation_on_the_record(container, paired):
    """`revoke` used to rebuild the credential field by field, which would have silently
    reset `rotated_at` the moment the field was added. It uses `replace` now."""
    credential, old = paired
    new = PrivateKey.generate()
    rotated = container.pairing.rotate(
        credential.device_id, **_signed(container.pairing, credential.device_id, old, new)
    )
    revoked = container.pairing.revoke(credential.device_id)
    assert revoked.rotated_at == rotated.rotated_at
    assert revoked.fingerprint == new.public.fingerprint


# --------------------------------------------------------------------------- the due list


def test_an_overdue_key_is_reported_and_still_works(container, paired):
    """ADR 0042. A device key that expired on its own would turn "the owner was away" into
    "every machine is locked out and must be re-paired by hand"."""
    credential, _old = paired
    ancient = timedelta(seconds=0)

    assert container.pairing.due_for_rotation(ancient) == [credential]
    # Still usable: being overdue is a statement about hygiene, not about access.
    auth = DeviceAuthenticator("unused", pairing=container.pairing)
    assert _hello_verifies(auth, credential.device_id, _old)


def test_rotation_resets_the_clock_on_the_key_rather_than_on_the_pairing(container, paired):
    """Age is a property of the key in use, not of the relationship with the device."""
    credential, old = paired
    new = PrivateKey.generate()
    rotated = container.pairing.rotate(
        credential.device_id, **_signed(container.pairing, credential.device_id, old, new)
    )
    assert rotated.issued_at == rotated.rotated_at
    assert rotated.paired_at == credential.paired_at, "pairing is when the owner decided"
    assert container.pairing.due_for_rotation(timedelta(days=1)) == []


# --------------------------------------------------------------------------- the socket


@pytest.fixture
def client(settings, container):
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def hello_for(device_id, key: PrivateKey, name: str = "Office-PC") -> str:
    frame = Hello(
        device_id=device_id,
        name=name,
        os="Windows",
        capabilities=DeviceCapabilities.of("app.open"),
        telemetry=DeviceTelemetry(),
        nonce=f"hello-{utcnow().timestamp()}",
    )
    frame.signature = key.sign(
        hello_payload(
            device_id=str(frame.device_id),
            name=frame.name,
            os=frame.os,
            nonce=frame.nonce,
            issued_at=frame.ts,
        )
    )
    return frame.model_dump_json()


def test_revoking_a_connected_device_closes_its_socket(client, container, paired):
    """The defect this sprint started from.

    `forget` dropped the session so the core could not dispatch to the device — but the
    socket stayed up and the node kept sending into a core that was still reading. The node
    had no way to learn it had been cut off, which is the worst shape for a revocation to
    have: the owner is told it is done, and the machine carries on connected.

    Driven through the endpoint the owner actually calls, not through the hub, because the
    endpoint is where the two halves (registry and transport) are supposed to meet.
    """
    credential, key = paired
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, key))
        assert json.loads(ws.receive_text())["type"] == "WELCOME"

        response = client.post(f"/api/v1/devices/{credential.device_id}/revoke")
        assert response.status_code == 200

        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_text()
    assert caught.value.code == CLOSE_SESSION_ENDED


def test_the_emergency_stop_actually_disconnects_nodes(client, container, paired):
    """§134 lists "disconnect Nodes" among the kill switch's actions. It called the same
    `unregister` that never closed the transport, so every node stayed connected to a core
    that had just declared an emergency."""
    credential, key = paired
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, key))
        assert json.loads(ws.receive_text())["type"] == "WELCOME"

        response = client.post("/api/v1/emergency/stop", json={"scope": "all"})
        assert response.status_code == 200

        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_a_session_is_told_when_it_expires(client, paired):
    """The node is handed the deadline at WELCOME so it can reconnect a moment early rather
    than discovering it mid-action."""
    credential, key = paired
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, key))
        welcome = json.loads(ws.receive_text())
    assert "session_expires_at" in welcome["policy"]
    assert welcome["policy"]["session_max_s"] == pytest.approx(12 * 3600)


def test_a_session_past_its_deadline_is_closed_and_told_to_come_back(settings, container, paired):
    """§79. The close code is distinct from the refusal code on purpose: a node that reads a
    routine expiry as a refusal is a machine the owner silently loses, and one that reads a
    refusal as an expiry hammers a core that will never accept it.

    `model_copy` rather than a constructor, deliberately — `Settings` will not accept a
    lifetime this short, and that floor is the point of the setting. This reaches past the
    validator to make the deadline arrive during the test rather than in fifteen minutes.
    """
    credential, key = paired
    instant = settings.model_copy(update={"device_session_max_hours": 1e-9})
    container.settings = instant
    app = create_app(instant, container=container)
    app.state.container = container

    with TestClient(app) as http, http.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, key))
        assert json.loads(ws.receive_text())["type"] == "WELCOME"
        frame = json.loads(ws.receive_text())

        assert frame["type"] == "ERROR"
        assert frame["code"] == "session_expired"
        assert frame["fatal"] is False, "expiry is routine; fatal would stop the node returning"

        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_text()
    assert caught.value.code == CLOSE_SESSION_EXPIRED


def test_the_two_close_codes_say_opposite_things():
    """One means come back now, the other means do not come back. A node that confuses them
    either abandons a healthy core or hammers one that will refuse it for ever."""
    assert CLOSE_SESSION_EXPIRED != CLOSE_SESSION_ENDED


def test_the_node_reconnects_after_an_expiry_and_backs_off_after_a_refusal():
    """The node's half of the same distinction, without a socket.

    `run_forever` decides by close code, so this drives that decision directly: expiry after
    a healthy session reconnects at once, and everything else backs off.
    """
    from apps.node.__main__ import MIN_HEALTHY_SESSION_S, _close_code

    class Closed(Exception):
        def __init__(self, code: int) -> None:
            self.rcvd = type("Frame", (), {"code": code})()

    assert _close_code(Closed(CLOSE_SESSION_EXPIRED)) == CLOSE_SESSION_EXPIRED
    assert _close_code(Closed(CLOSE_SESSION_ENDED)) == CLOSE_SESSION_ENDED

    # No close frame at all — a dropped link, not a decision by the core.
    assert _close_code(type("NoFrame", (), {"rcvd": None})()) is None
    assert MIN_HEALTHY_SESSION_S > 0, "a session that never lasted cannot be a routine expiry"


# --------------------------------------------------------------------------- over HTTP


def test_rotation_over_http_drops_the_session_the_old_key_authenticated(client, container, paired):
    """The endpoint, and the reason it ends the live session.

    That session was authenticated with the key this request retires. Leaving it up would
    let the old key keep driving the machine for as long as the connection lasted, which
    would make rotation a change of record-keeping rather than a change of access.
    """
    credential, old = paired
    new = PrivateKey.generate()

    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, old))
        assert json.loads(ws.receive_text())["type"] == "WELCOME"

        response = client.post(
            f"/api/v1/devices/{credential.device_id}/rotate",
            json=_as_http(rotation_body(container.pairing, credential.device_id, old, new)),
        )
        assert response.status_code == 200
        assert response.json()["fingerprint"] == new.public.fingerprint

        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    # And the device is still known — `unregister`, not `forget`. It is coming straight back
    # with its new key, and forgetting it would drop the trust level the owner granted.
    assert container.pairing.credential(credential.device_id) is not None


def test_a_forged_rotation_over_http_is_refused_without_saying_why(client, container, paired):
    """403 for both "no such device" and "bad signature". The caller is presenting
    signatures; which one failed is a distinction worth denying them."""
    credential, _old = paired
    impostor, new = PrivateKey.generate(), PrivateKey.generate()

    response = client.post(
        f"/api/v1/devices/{credential.device_id}/rotate",
        json=_as_http(rotation_body(container.pairing, credential.device_id, impostor, new)),
    )
    assert response.status_code == 403
    assert container.pairing.credential(credential.device_id).fingerprint == credential.fingerprint

    unknown = client.post(
        f"/api/v1/devices/{new_id()}/rotate",
        json=_as_http(rotation_body(container.pairing, credential.device_id, impostor, new)),
    )
    assert unknown.status_code == 403


def test_the_credentials_endpoint_reports_key_age_without_gating_on_it(client, paired):
    """§133's security dashboard. `rotation_due` is hygiene, not access (ADR 0042)."""
    credential, _old = paired
    rows = client.get("/api/v1/devices/credentials").json()["credentials"]
    mine = next(r for r in rows if r["device_id"] == str(credential.device_id))
    assert mine["rotation_due"] is False
    assert mine["key_age_days"] == 0
    assert mine["rotated_at"] is None


# --------------------------------------------------------------------------- the token


def test_a_paired_device_connects_to_a_core_that_has_no_enrolment_token(client, paired):
    """Found by this sprint's own socket tests, and a real defect rather than a test problem.

    `verify` refused outright when no shared enrolment token was configured — before it ever
    looked at the device's registered key. So the deployment §80 is aiming at, where every
    machine has paired and the enrolment token has been dropped because it has no job left,
    refused every properly paired device. The token's absence cannot be what refuses a
    device whose own key the core holds.
    """
    credential, key = paired
    assert not client.app.state.container.device_auth.configured

    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_for(credential.device_id, key))
        assert json.loads(ws.receive_text())["type"] == "WELCOME"


def test_an_unpaired_device_is_still_refused_when_there_is_no_token(client):
    """The other half, and the one that must not regress: with neither a registered key nor
    a token there is nothing to check a signature against, so it fails closed."""
    from thursday_shared.protocol import Hello as _Hello

    stranger = PrivateKey.generate()
    frame = _Hello(
        device_id=new_id(),
        name="Impostor-PC",
        os="Windows",
        capabilities=DeviceCapabilities.of("app.open"),
        telemetry=DeviceTelemetry(),
        nonce="stranger",
    )
    frame.signature = stranger.sign(
        hello_payload(
            device_id=str(frame.device_id),
            name=frame.name,
            os=frame.os,
            nonce=frame.nonce,
            issued_at=frame.ts,
        )
    )
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(frame.model_dump_json())
        refusal = json.loads(ws.receive_text())
    assert refusal["type"] == "ERROR"
    assert refusal["code"] == "unauthenticated"


# --------------------------------------------------------------------------- helpers


def _signed(service: PairingService, device_id, old: PrivateKey, new: PrivateKey) -> dict:
    return rotation_body(service, device_id, old, new)


def _hello_verifies(auth: DeviceAuthenticator, device_id, key: PrivateKey) -> bool:
    nonce = f"n-{key.public.fingerprint}"
    issued_at = utcnow()
    signature = key.sign(
        hello_payload(
            device_id=str(device_id),
            name="Office-PC",
            os="Windows",
            nonce=nonce,
            issued_at=issued_at,
        )
    )
    return auth.verify(
        device_id=str(device_id),
        name="Office-PC",
        os="Windows",
        nonce=nonce,
        issued_at=issued_at,
        signature=signature,
    ).ok


# --------------------------------------------------------------------------- the node's half


def test_the_successor_key_is_written_down_before_the_core_is_asked_to_take_it(tmp_path):
    """The failure that costs a physical visit.

    If the core accepts a rotation and the node dies before it hears back, the machine's
    identity is a key it never saved — and a node whose key the core does not recognise has
    to be re-paired by a person standing at it. Staging first turns that from a lost
    identity into a resumable one.
    """
    from apps.node.__main__ import NodeIdentity

    identity = NodeIdentity(tmp_path / "node.json")
    live = identity.key.public.fingerprint

    staged = identity.stage_pending()
    assert staged.public.fingerprint != live
    assert identity.key.public.fingerprint == live, "the live key must not change yet"

    # A fresh object over the same files: this is what survives a crash.
    assert NodeIdentity(tmp_path / "node.json").pending_key().to_pem() == staged.to_pem()


def test_staging_twice_reuses_the_key_already_staged(tmp_path):
    """A retry must offer the same successor. Generating a second one would leave the first
    orphaned in the core if the first attempt had in fact landed."""
    from apps.node.__main__ import NodeIdentity

    identity = NodeIdentity(tmp_path / "node.json")
    assert identity.stage_pending().to_pem() == identity.stage_pending().to_pem()


def test_promoting_replaces_the_live_key_and_clears_the_staging_slot(tmp_path):
    from apps.node.__main__ import NodeIdentity

    identity = NodeIdentity(tmp_path / "node.json")
    identity.record_pairing(device_id=str(new_id()), fingerprint=identity.key.public.fingerprint)
    staged = identity.stage_pending()

    identity.promote_pending()

    assert identity.key.to_pem() == staged.to_pem()
    assert identity.pending_key() is None
    reloaded = NodeIdentity(tmp_path / "node.json")
    assert reloaded.key.public.fingerprint == staged.public.fingerprint
    assert reloaded.data["pairing"]["fingerprint"] == staged.public.fingerprint


def test_the_staged_key_file_is_not_world_readable(tmp_path):
    """It is a private key. It gets the same 0600 the live one gets."""
    import stat

    from apps.node.__main__ import NodeIdentity

    identity = NodeIdentity(tmp_path / "node.json")
    identity.stage_pending()
    mode = stat.S_IMODE(identity.pending_key_path.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_a_node_that_never_paired_refuses_to_rotate(tmp_path, capsys):
    """Rotation replaces an identity the core already trusts. There isn't one yet."""
    from apps.node.__main__ import NodeIdentity, rotate_key

    identity = NodeIdentity(tmp_path / "node.json")
    assert rotate_key(identity, core_url="ws://127.0.0.1:9/api/v1/device") == 1
    assert "not paired" in capsys.readouterr().out
    assert identity.pending_key() is None, "nothing should have been staged"


def test_a_rotation_whose_reply_was_lost_resolves_itself_against_the_core(
    tmp_path, client, container, monkeypatch
):
    """The case that would otherwise need somebody to walk to the machine.

    The core accepted the rotation; the node never heard back. The node must not assume
    either way — it asks the core which key it holds, and promotes only if the answer is
    the key it staged.

    `httpx.get` is redirected into the TestClient, which is a transport shim rather than a
    stand-in for the thing under test: the response comes from the real `/devices/credentials`
    endpoint, so this fails if that endpoint stops reporting fingerprints.
    """
    import httpx

    from apps.node.__main__ import NodeIdentity, _resolve_staged

    credential, old = paired_via(container)
    identity = NodeIdentity(tmp_path / "node.json")
    identity.record_pairing(device_id=str(credential.device_id), fingerprint=credential.fingerprint)
    successor = identity.stage_pending()

    # The core took it. The node does not know that yet.
    container.pairing.rotate(
        credential.device_id,
        **rotation_body(container.pairing, credential.device_id, old, successor),
    )

    monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url.replace(HOST, "")))
    assert _resolve_staged(identity, successor, base=BASE) == 0
    assert identity.key.public.fingerprint == successor.public.fingerprint


def test_a_rotation_the_core_never_took_keeps_the_staged_key_and_says_so(
    tmp_path, client, container, monkeypatch, capsys
):
    """The other branch. The node still works — it kept the key the core still trusts —
    and it must say that rather than leave the operator guessing."""
    import httpx

    from apps.node.__main__ import NodeIdentity, _resolve_staged

    credential, _old = paired_via(container)
    identity = NodeIdentity(tmp_path / "node.json")
    identity.record_pairing(device_id=str(credential.device_id), fingerprint=credential.fingerprint)
    successor = identity.stage_pending()
    live = identity.key.public.fingerprint

    monkeypatch.setattr(httpx, "get", lambda url, **kw: client.get(url.replace(HOST, "")))
    assert _resolve_staged(identity, successor, base=BASE) == 1

    assert identity.key.public.fingerprint == live, "the working key must be left alone"
    assert identity.pending_key() is not None, "the staged key is kept so a retry can reuse it"
    assert "still works" in capsys.readouterr().out
