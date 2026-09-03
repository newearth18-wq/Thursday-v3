"""The device WebSocket, exercised as an attacker would (T3, ADR 0013).

`tests/unit/test_device_auth.py` proves the authenticator's logic. This proves the gateway
actually calls it, in the environment a developer runs — the version of this check that
only fired in production was, in practice, no check at all.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from thursday_api.app import create_app
from thursday_security.device_auth import sign, signing_payload
from thursday_shared.ids import new_id
from thursday_shared.models import DeviceCapabilities, DeviceTelemetry, utcnow
from thursday_shared.protocol import Hello

TOKEN = "the-configured-enrolment-token"


@pytest.fixture
def client(settings, container):
    container.device_auth = __import__(
        "thursday_security.device_auth", fromlist=["DeviceAuthenticator"]
    ).DeviceAuthenticator(TOKEN)
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


def hello_frame(*, token: str | None, name: str = "Impostor-PC") -> str:
    frame = Hello(
        device_id=new_id(),
        name=name,
        os="Windows",
        capabilities=DeviceCapabilities.of("app.open"),
        telemetry=DeviceTelemetry(),
        nonce="nonce-1",
    )
    if token is not None:
        frame.signature = sign(
            token,
            signing_payload(
                device_id=str(frame.device_id),
                name=frame.name,
                os=frame.os,
                nonce=frame.nonce,
                issued_at=frame.ts,
            ),
        )
    return frame.model_dump_json()


def test_a_node_with_the_token_is_welcomed(client, container):
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_frame(token=TOKEN, name="Office-PC"))
        welcome = json.loads(ws.receive_text())
        assert welcome["type"] == "WELCOME"
    assert any(d.name == "Office-PC" for d in container.hub.all())


def test_a_node_without_the_token_never_becomes_a_device(client, container):
    """The important half. A registered impostor could report `verified: true` for work it
    never did, and nothing downstream would know to doubt it."""
    before = len(container.hub.all())
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_frame(token="wrong-token"))
        error = json.loads(ws.receive_text())
        assert error["type"] == "ERROR"
        assert error["code"] == "unauthenticated"
        # The reason is logged for the operator, not returned: telling an unauthenticated
        # caller which check failed helps only whoever is probing.
        assert "signature" not in error["message"]
    assert len(container.hub.all()) == before


def test_an_unsigned_hello_never_becomes_a_device(client, container):
    before = len(container.hub.all())
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_frame(token=None))
        assert json.loads(ws.receive_text())["code"] == "unauthenticated"
    assert len(container.hub.all()) == before


def test_the_rejection_holds_outside_production(client, container, settings):
    """This check used to close the socket only when environment == "production", which
    meant every development and staging deployment trusted anything that connected."""
    assert settings.environment != "production"
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_frame(token="wrong-token"))
        assert json.loads(ws.receive_text())["code"] == "unauthenticated"


def test_the_token_never_reaches_the_logs(client, container, capsys):
    with client.websocket_connect("/api/v1/device") as ws:
        ws.send_text(hello_frame(token=TOKEN, name="Office-PC"))
        ws.receive_text()
    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err


# ------------------------------------------------------------------ REST enrolment


def registration(*, token: str | None, name: str = "Office-PC") -> dict:

    body = {
        "device_id": str(new_id()),
        "name": name,
        "kind": "desktop",
        "os": "Windows",
        "capabilities": ["app.open", "file.search"],
        "nonce": f"nonce-{new_id()}",
        "issued_at": utcnow().isoformat(),
    }
    body["signature"] = (
        ""
        if token is None
        else sign(
            token,
            signing_payload(
                device_id=body["device_id"],
                name=body["name"],
                os=body["os"],
                nonce=body["nonce"],
                issued_at=utcnow().fromisoformat(body["issued_at"]),
            ),
        )
    )
    return body


def test_registering_needs_the_same_token_the_socket_does(client, container):
    """One trusted-device set, one check. A second door is worth exactly as much as the
    weaker of the two."""
    refused = client.post("/api/v1/devices/register", json=registration(token="wrong"))
    assert refused.status_code == 401
    assert not container.hub.all()


def test_a_registered_device_is_not_yet_reachable(client, container):
    """Enrolment and connection are different facts. A device listed as reachable that
    cannot receive a command would be selected by the router and fail three steps in."""
    body = registration(token=TOKEN)
    accepted = client.post("/api/v1/devices/register", json=body)
    assert accepted.status_code == 200

    device = accepted.json()["device"]
    assert device["status"] == "offline"
    assert accepted.json()["command_channel"] == "/api/v1/device"
    assert container.hub.online() == []


def test_a_heartbeat_for_an_unknown_device_is_a_404(client):
    body = registration(token=TOKEN)
    beat = client.post(
        "/api/v1/devices/heartbeat",
        json={k: v for k, v in body.items() if k != "capabilities" and k != "kind"},
    )
    assert beat.status_code == 404


def test_an_unsigned_heartbeat_cannot_hold_a_dead_device_online(client):
    body = registration(token=TOKEN)
    client.post("/api/v1/devices/register", json=body)

    forged = {k: v for k, v in body.items() if k not in ("capabilities", "kind")}
    forged["signature"] = ""
    assert client.post("/api/v1/devices/heartbeat", json=forged).status_code == 401
