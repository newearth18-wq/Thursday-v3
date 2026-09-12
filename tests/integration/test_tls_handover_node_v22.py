"""The whole path: the operator signs, the core serves, a node follows (§117, ADR 0071).

`test_tls_handover_v22.py` covers the statement on its own. This is about whether the three
halves meet — because the interesting failures in a rotation are never in the cryptography,
they are in a file nobody wrote, an endpoint that 404s, or a node that gives up before asking.

The scenario throughout is the one that used to cost a walk to every machine: the core's
certificate is replaced with one on a **new key**, and every paired node's pin stops matching
at the same moment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_api.routers.devices import HANDOVER_FILE
from thursday_security.handover import HandOver
from thursday_security.pinning import spki_pin

from apps.node.__main__ import NodeClient, NodeIdentity
from apps.server.__main__ import sign_handover
from tests.integration.test_pinning import make_certificate


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


@pytest.fixture
def core_keys(tmp_path):
    """A retiring certificate and an incoming one on a different key — a real rotation."""
    old_cert, old_key, old_pin, _ = make_certificate(tmp_path, "retiring")
    new_cert, _, new_pin, _ = make_certificate(tmp_path, "incoming")
    return old_cert, old_key, old_pin, new_cert, new_pin


def paired(identity: NodeIdentity, pin: str) -> NodeIdentity:
    import uuid

    identity.record_pairing(
        device_id=str(uuid.uuid4()),
        fingerprint=identity.fingerprint,
        core="wss://core.test",
        pin=pin,
    )
    return identity


def node(identity: NodeIdentity) -> NodeClient:
    return NodeClient(
        core_url="wss://core.test/api/v1/device",
        name="Office-PC",
        identity=identity,
        executor=None,
        token="shared-enrolment-token",
    )


# ------------------------------------------------------------------------- the signing tool


def test_the_tool_publishes_a_chain_a_node_can_read(tmp_path, core_keys):
    _, old_key, old_pin, new_cert, new_pin = core_keys
    data_dir = tmp_path / "var"

    assert (
        sign_handover(
            retiring_key=old_key, incoming_cert=new_cert, compromised=False, data_dir=data_dir
        )
        == 0
    )

    stored = json.loads((data_dir / HANDOVER_FILE).read_text())["handovers"]
    link = HandOver.from_dict(stored[0])
    assert link.retiring_pin == old_pin
    assert link.next_pin == new_pin


def test_the_tool_writes_nothing_when_the_key_was_compromised(tmp_path, core_keys):
    """The refusal has to leave no file behind.

    A hand-over that existed but was "not recommended" would be followed by every node that
    found it, which is the outcome the refusal is for. See `handover.sign` for why a
    compromised key cannot be handed over: whoever else holds it can sign one too.
    """
    _, old_key, _, new_cert, _ = core_keys
    data_dir = tmp_path / "var"

    assert (
        sign_handover(
            retiring_key=old_key, incoming_cert=new_cert, compromised=True, data_dir=data_dir
        )
        == 1
    )
    assert not (data_dir / HANDOVER_FILE).exists()


def test_the_tool_refuses_to_fork_the_chain(tmp_path, core_keys):
    """Two hand-overs from one key send different nodes to different keys.

    `follow` takes the first branch it finds, so a fork does not fail loudly — it strands
    whichever nodes walked the abandoned one, and the symptom weeks later is "some machines
    never came back". Refused here, where it is still one file and one person.
    """
    _, old_key, _, new_cert, _ = core_keys
    other_cert, _, _, _ = make_certificate(tmp_path, "another")
    data_dir = tmp_path / "var"
    sign_handover(
        retiring_key=old_key, incoming_cert=new_cert, compromised=False, data_dir=data_dir
    )

    assert (
        sign_handover(
            retiring_key=old_key, incoming_cert=other_cert, compromised=False, data_dir=data_dir
        )
        == 1
    )

    stored = json.loads((data_dir / HANDOVER_FILE).read_text())["handovers"]
    assert len(stored) == 1


def test_an_encrypted_key_is_named_rather_than_raising(tmp_path, core_keys):
    from cryptography.hazmat.primitives.asymmetric import ed25519

    _, _, _, new_cert, _ = core_keys
    locked = tmp_path / "locked.key"
    locked.write_bytes(
        ed25519.Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"passphrase"),
        )
    )

    assert (
        sign_handover(
            retiring_key=locked, incoming_cert=new_cert, compromised=False, data_dir=tmp_path
        )
        == 1
    )


# ------------------------------------------------------------------------------ the endpoint


async def test_a_core_that_never_rotated_answers_with_an_empty_list(client):
    """Not a 404. "No key has ever been handed over" is the ordinary state of a healthy
    deployment, and a node should read it as nothing to follow rather than as a fault."""
    response = await client.get("/api/v1/devices/tls-handover")

    assert response.status_code == 200
    assert response.json()["handovers"] == []


async def test_the_endpoint_serves_what_the_tool_wrote(client, settings, core_keys):
    _, old_key, old_pin, new_cert, new_pin = core_keys
    sign_handover(
        retiring_key=old_key, incoming_cert=new_cert, compromised=False, data_dir=settings.data_dir
    )

    response = await client.get("/api/v1/devices/tls-handover")

    [served] = response.json()["handovers"]
    assert HandOver.from_dict(served).retiring_pin == old_pin
    assert served["next_pin"] == new_pin


async def test_the_endpoint_is_reachable_and_not_read_as_a_device_id(client):
    """`/devices/credentials` was once shadowed by `/devices/{device_id}` and nobody noticed
    because it was still in the schema. The same trap is one line away from this route."""
    response = await client.get("/api/v1/devices/tls-handover")

    assert response.status_code == 200
    assert "detail" not in response.json()


# ---------------------------------------------------------------------------- the node follows


async def test_a_node_follows_the_published_hand_over_and_repins(
    tmp_path, settings, core_keys, monkeypatch
):
    _, old_key, old_pin, new_cert, new_pin = core_keys
    sign_handover(
        retiring_key=old_key, incoming_cert=new_cert, compromised=False, data_dir=settings.data_dir
    )
    chain = [
        HandOver.from_dict(item)
        for item in json.loads((settings.data_dir / HANDOVER_FILE).read_text())["handovers"]
    ]
    identity = paired(NodeIdentity(tmp_path / "node.json"), old_pin)
    client = node(identity)
    monkeypatch.setattr("apps.node.__main__.published_handovers", lambda base, **kw: chain)

    assert await client._follow_handover() is True

    assert NodeIdentity(tmp_path / "node.json").core_pin.value == new_pin


async def test_a_node_keeps_its_pin_when_the_chain_does_not_verify(
    tmp_path, core_keys, monkeypatch
):
    """The case that has to stay dangerous. An impostor presenting a different key can also
    serve a document; it just cannot sign one with the key this node pinned."""
    _, _, old_pin, _, _ = core_keys
    _, impostor_key, _, _ = make_certificate(tmp_path, "impostor")
    elsewhere, _, _, _ = make_certificate(tmp_path, "elsewhere")
    forged = _sign_link(impostor_key, elsewhere)
    identity = paired(NodeIdentity(tmp_path / "node.json"), old_pin)
    client = node(identity)
    monkeypatch.setattr("apps.node.__main__.published_handovers", lambda base, **kw: [forged])

    assert await client._follow_handover() is False

    assert identity.core_pin.value == old_pin
    # And nothing was written at all: a pin that moved and moved back would still have left
    # a history entry, and the node would have connected to the impostor in between.
    assert "pin_history" not in json.loads((tmp_path / "node.json").read_text())["pairing"]


async def test_a_node_asks_once_per_pin_and_not_once_per_reconnection(
    tmp_path, core_keys, monkeypatch
):
    """A node whose pin genuinely does not match reconnects for as long as it runs. Asking
    every time would be a node hammering its own core on the strength of a failure."""
    _, _, old_pin, _, _ = core_keys
    identity = paired(NodeIdentity(tmp_path / "node.json"), old_pin)
    client = node(identity)
    asked = []

    def fetch(base, **kw):
        asked.append(base)
        return []

    monkeypatch.setattr("apps.node.__main__.published_handovers", fetch)

    assert await client._follow_handover() is False
    assert await client._follow_handover() is False

    assert len(asked) == 1


async def test_a_node_that_never_recorded_a_pin_does_not_go_looking(tmp_path, monkeypatch):
    """A node paired over plaintext has no pin, so there is nothing for a hand-over to move
    and no question to ask."""
    identity = paired(NodeIdentity(tmp_path / "node.json"), "")
    client = node(identity)
    monkeypatch.setattr(
        "apps.node.__main__.published_handovers",
        lambda base, **kw: pytest.fail("asked for a hand-over with no pin to move"),
    )

    assert await client._follow_handover() is False


async def test_an_unreachable_core_is_a_refusal_rather_than_a_crash(
    tmp_path, core_keys, monkeypatch
):
    import httpx

    _, _, old_pin, _, _ = core_keys
    identity = paired(NodeIdentity(tmp_path / "node.json"), old_pin)
    client = node(identity)

    def boom(base, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr("apps.node.__main__.published_handovers", boom)

    assert await client._follow_handover() is False
    assert identity.core_pin.value == old_pin


# ------------------------------------------------------------------------------- the record


def test_the_previous_pin_is_kept_so_the_owner_can_see_what_changed(tmp_path, core_keys):
    """Nothing reads this back. It is here because "what did this machine trust, and when did
    that stop" is a question the file should answer without a log that has rotated away."""
    _, old_key, old_pin, new_cert, new_pin = core_keys
    identity = paired(NodeIdentity(tmp_path / "node.json"), old_pin)
    link = _sign_link(old_key, new_cert)

    identity.adopt_pin(new_pin, after=[link])

    pairing = json.loads((tmp_path / "node.json").read_text())["pairing"]
    assert pairing["pin"] == new_pin
    [record] = pairing["pin_history"]
    assert record["pin"] == old_pin
    assert record["handovers"][0]["next_pin"] == new_pin


def _sign_link(key_path: Path, cert_path: Path) -> HandOver:
    from cryptography import x509
    from thursday_security.handover import sign

    return sign(
        key_path.read_text(),
        next_pin=spki_pin(
            x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(
                serialization.Encoding.DER
            )
        ),
        compromised=False,
    )
