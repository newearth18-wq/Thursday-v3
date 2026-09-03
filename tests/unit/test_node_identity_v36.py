"""The node's half of pairing (§82, Sprint 36).

The core can only refuse a bad signature; it cannot make a node sign well. So these tests
are about the node's own conduct: that it generates a key rather than reusing a shared one,
that the private half stays on disk with a mode that means something, that what goes over
the wire is the public half, and — the one that matters most — that a node which has paired
never signs with the enrolment token again, even when the key is refused.
"""

from __future__ import annotations

import json
import stat
import uuid
from datetime import UTC, datetime

import pytest
from thursday_security.device_auth import DeviceAuthenticator, sign, signing_payload
from thursday_security.keys import PublicKey, hello_payload, pairing_payload
from thursday_security.pairing import PairingService
from thursday_shared.models import DeviceCapabilities, DeviceTelemetry
from thursday_shared.protocol import Hello

from apps.node.__main__ import NodeClient, NodeIdentity, api_base, pairing_request

TOKEN = "shared-enrolment-token"


@pytest.fixture
def identity(tmp_path):
    return NodeIdentity(tmp_path / "node.json")


def client(identity, *, token: str = TOKEN) -> NodeClient:
    """A NodeClient with no executor: nothing here reaches the device layer."""
    return NodeClient(
        core_url="ws://core.test/api/v1/device",
        name="Office-PC",
        identity=identity,
        executor=None,
        token=token,
    )


def hello_frame(identity, *, name: str = "Office-PC", os: str = "Windows", **over) -> Hello:
    return Hello(
        device_id=identity.device_id,
        name=name,
        kind="desktop",
        os=os,
        capabilities=DeviceCapabilities(),
        telemetry=DeviceTelemetry(),
        **{"nonce": "n1", **over},
    )


# --------------------------------------------------------------------------- the key file


def test_a_node_generates_its_own_key_on_first_use(identity):
    assert not identity.key_path.exists()
    fingerprint = identity.fingerprint
    assert identity.key_path.exists()
    assert fingerprint == identity.key.public.fingerprint


def test_the_key_is_the_same_one_across_restarts(tmp_path):
    first = NodeIdentity(tmp_path / "node.json")
    original = first.fingerprint
    second = NodeIdentity(tmp_path / "node.json")
    assert second.fingerprint == original


def test_the_key_file_is_not_readable_by_anyone_else(identity):
    identity.key  # noqa: B018 — generating it is the point
    mode = stat.S_IMODE(identity.key_path.stat().st_mode)
    assert mode == 0o600, f"node.key is {oct(mode)}"


def test_the_private_key_is_not_in_the_file_the_operator_will_open(identity):
    """`node.json` is the file people paste into bug reports. The credential is elsewhere."""
    identity.key  # noqa: B018
    identity.record_pairing(device_id=str(uuid.uuid4()), fingerprint=identity.fingerprint)
    body = identity.path.read_text()
    assert "PRIVATE KEY" not in body
    assert identity.key.to_pem() not in body
    assert json.loads(body)["pairing"]["fingerprint"] == identity.fingerprint


# --------------------------------------------------------------------------- pair/start


def test_the_pairing_request_proves_possession_and_carries_only_the_public_half(identity):
    body = pairing_request(identity, name="Office-PC", os_name="Windows", hostname="office")

    assert body["public_key"] == identity.key.public.encoded
    assert "PRIVATE KEY" not in json.dumps(body)

    issued_at = datetime.fromisoformat(body["issued_at"])
    assert PublicKey(encoded=body["public_key"]).verify(
        pairing_payload(
            public_key=body["public_key"],
            name=body["name"],
            os=body["os"],
            hostname=body["hostname"],
            nonce=body["nonce"],
            issued_at=issued_at,
        ),
        body["signature"],
    )


def test_a_real_core_accepts_the_request_the_node_actually_builds(identity):
    """The two sides are written from the same `pairing_payload`, and this is the test that
    would fail if they ever stopped being."""
    service = PairingService()
    body = pairing_request(identity, name="Office-PC", os_name="Windows", hostname="office")
    pending = service.start(
        public_key=body["public_key"],
        name=body["name"],
        os=body["os"],
        hostname=body["hostname"],
        nonce=body["nonce"],
        issued_at=datetime.fromisoformat(body["issued_at"]),
        signature=body["signature"],
    )
    credential = service.complete(pending.code)
    assert credential.fingerprint == identity.fingerprint


def test_each_pairing_request_is_fresh(identity):
    first = pairing_request(identity, name="PC", os_name="Windows", hostname="h")
    second = pairing_request(identity, name="PC", os_name="Windows", hostname="h")
    assert first["nonce"] != second["nonce"]
    assert first["signature"] != second["signature"]


# --------------------------------------------------------------------------- signing HELLO


def test_an_unpaired_node_signs_with_the_enrolment_token(identity):
    hello = hello_frame(identity)
    signature = client(identity).sign_hello(hello)
    assert signature == sign(
        TOKEN,
        signing_payload(
            device_id=str(hello.device_id),
            name=hello.name,
            os=hello.os,
            nonce=hello.nonce,
            issued_at=hello.ts,
        ),
    )


def test_a_paired_node_signs_with_its_own_key(identity):
    identity.record_pairing(device_id=str(uuid.uuid4()), fingerprint=identity.fingerprint)
    hello = hello_frame(identity)
    signature = client(identity).sign_hello(hello)

    assert identity.key.public.verify(
        hello_payload(
            device_id=str(hello.device_id),
            name=hello.name,
            os=hello.os,
            nonce=hello.nonce,
            issued_at=hello.ts,
        ),
        signature,
    )


def test_a_paired_node_never_falls_back_to_the_token(identity):
    """Even with the token still in the environment. A node that retries with the shared
    secret when its key is refused has handed the token back its full power."""
    identity.record_pairing(device_id=str(uuid.uuid4()), fingerprint=identity.fingerprint)
    hello = hello_frame(identity)
    node = client(identity)

    token_signature = sign(
        TOKEN,
        signing_payload(
            device_id=str(hello.device_id),
            name=hello.name,
            os=hello.os,
            nonce=hello.nonce,
            issued_at=hello.ts,
        ),
    )
    assert node.sign_hello(hello) != token_signature
    assert node.sign_hello(hello) != token_signature  # and not on the second try either


def test_a_paired_node_adopts_the_device_id_the_core_assigned(identity):
    """The core names the device, not the node. A node that could name itself could name
    itself as the server, and the owner confirming a code on their laptop would be
    registering an attacker's key against a machine they never touched."""
    bootstrap_id = identity.device_id
    assigned = uuid.uuid4()
    identity.record_pairing(device_id=str(assigned), fingerprint=identity.fingerprint)

    assert identity.device_id == assigned
    assert identity.device_id != bootstrap_id
    assert json.loads(identity.path.read_text())["device_id"] == str(bootstrap_id)


def test_the_node_and_the_core_agree_end_to_end(identity):
    """Node pairs, node connects, core accepts — then the owner revokes and the same node,
    signing exactly as before, is refused."""
    service = PairingService()
    auth = DeviceAuthenticator(TOKEN, pairing=service)

    body = pairing_request(identity, name="Office-PC", os_name="Windows", hostname="office")
    pending = service.start(
        public_key=body["public_key"],
        name=body["name"],
        os=body["os"],
        hostname=body["hostname"],
        nonce=body["nonce"],
        issued_at=datetime.fromisoformat(body["issued_at"]),
        signature=body["signature"],
    )
    credential = service.complete(pending.code)
    identity.record_pairing(device_id=str(credential.device_id), fingerprint=identity.fingerprint)
    node = client(identity)

    def connect(nonce: str):
        hello = hello_frame(identity, nonce=nonce, ts=datetime.now(UTC))
        hello.signature = node.sign_hello(hello)
        return auth.verify(
            device_id=str(hello.device_id),
            name=hello.name,
            os=hello.os,
            nonce=hello.nonce,
            issued_at=hello.ts,
            signature=hello.signature,
        )

    assert connect("first").ok
    service.revoke(credential.device_id)
    refused = connect("second")
    assert not refused.ok
    assert "revoked" in refused.reason


def test_forgetting_a_pairing_keeps_the_key_so_the_fingerprint_still_means_something(identity):
    identity.record_pairing(device_id=str(uuid.uuid4()), fingerprint=identity.fingerprint)
    original = identity.fingerprint

    identity.forget_pairing()
    assert identity.paired is False
    assert identity.fingerprint == original
    assert "pairing" not in json.loads(identity.path.read_text())


# --------------------------------------------------------------------------- plumbing


@pytest.mark.parametrize(
    ("core", "expected"),
    [
        ("ws://127.0.0.1:8000/api/v1/device", "http://127.0.0.1:8000/api/v1"),
        ("wss://thursday.example/api/v1/device", "https://thursday.example/api/v1"),
        ("ws://127.0.0.1:8000/api/v1/device/", "http://127.0.0.1:8000/api/v1"),
        ("ws://127.0.0.1:8000", "http://127.0.0.1:8000/api/v1"),
    ],
)
def test_the_rest_base_is_derived_from_the_socket_url(core, expected):
    """One URL, not two. A node that pairs with one core and connects to another fails in a
    way nobody reads correctly."""
    assert api_base(core) == expected
