"""Who may talk to this API at all (§128, ADR 0073).

Sprint 100 rate-limited the HTTP surface and §23 said, in a sentence nobody had acted on, what
that left open: *"it is a bound on rate rather than authentication — of which there is still
none."*

The first test here is the one that made this a sprint rather than a paragraph. It was written
against the code as it stood, and it passed: an unauthenticated caller opened Chrome on the
owner's machine and got back `verified: true`. The device channel demands an Ed25519 signature
from every node before it carries a single frame; the HTTP API beside it ran the same catalogue
for anybody who could reach the port.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_core.auth import UNAUTHENTICATED, Refusal, admit, bearer, host_is_loopback
from thursday_core.container import api_token
from thursday_shared.errors import ConfigurationError

TOKEN = "an-owner-token-nothing-here-generated"


def serve(settings, container, *, peer: str = "127.0.0.1", host: str = "127.0.0.1:8000"):
    """A client that arrives from `peer`, saying `host`. Both are what the checks read."""
    app = create_app(settings, container=container)
    app.state.container = container
    return AsyncClient(
        transport=ASGITransport(app=app, client=(peer, 51234)),
        base_url=f"http://{host}",
    )


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("THURSDAY_SECRET_API_TOKEN", TOKEN)


# ------------------------------------------------------------------------ the hole, closed


async def test_a_caller_from_another_machine_cannot_open_an_app_on_this_one(
    settings, container, office_pc, adapter
):
    """Written first, against the code as it stood, where it failed by succeeding.

    `app.open` is AUTO, so the Permission Engine passes it and the action runs — which is
    correct once the caller is the owner and catastrophic while anybody is. The assertion is
    the property rather than the status code: **nothing ran**.
    """
    async with serve(settings, container, peer="203.0.113.5", host="thursday.example") as http:
        device = (await http.get("/api/v1/devices")).json()
        assert device.get("devices") is None, "listing devices needs the owner too"

        response = await http.post(
            f"/api/v1/devices/{office_pc.device_id}/actions",
            json={"action": "app.open", "args": {"app": "Chrome"}},
        )

    assert response.status_code == 403
    assert adapter.running == {}, "an unauthenticated caller opened an app"
    assert adapter.action_count == 0


async def test_the_owner_at_the_keyboard_still_needs_no_token(settings, container, office_pc):
    """The default deployment is one person's machine, and it must not need ceremony."""
    async with serve(settings, container) as http:
        response = await http.get("/api/v1/devices")

    assert response.status_code == 200
    assert [d["name"] for d in response.json()["devices"]] == ["Office-PC"]


async def test_a_page_in_the_owners_browser_cannot_reach_it_by_rebinding(settings, container):
    """Loopback is not a credential.

    A page at evil.example resolves its own hostname to 127.0.0.1 and talks to the API; the
    browser treats it as same-origin and sends the request. The peer is loopback and everything
    about it looks local — except the `Host` header, which is the whole signal.
    """
    async with serve(settings, container, peer="127.0.0.1", host="evil.example") as http:
        response = await http.get("/api/v1/devices")

    assert response.status_code == 400
    assert "token" not in response.text.lower(), "told an attacker how this is secured"


# --------------------------------------------------------------------------- with a token


async def test_a_token_admits_a_caller_from_anywhere(settings, container, office_pc, with_token):
    """Which is the point: a phone is a screen onto a Thursday running somewhere else."""
    async with serve(settings, container, peer="203.0.113.5", host="thursday.example") as http:
        response = await http.get("/api/v1/devices", headers={"authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


async def test_a_missing_token_and_a_wrong_one_are_refused_in_the_same_words(
    settings, container, with_token
):
    """`keys.py`'s rule: telling an unauthenticated caller *which* check failed helps only
    them. Both carry `WWW-Authenticate`, because a client that could send one should be told
    how."""
    async with serve(settings, container) as http:
        missing = await http.get("/api/v1/devices")
        wrong = await http.get("/api/v1/devices", headers={"authorization": "Bearer not-the-token"})

    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["message"] == wrong.json()["error"]["message"]
    assert missing.headers["www-authenticate"] == "Bearer"


async def test_the_token_is_required_at_the_keyboard_too(settings, container, with_token):
    """No loopback exemption. Setting a token is the owner saying they want authentication,
    and a local process — or a rebound page — is exactly what would use the exemption."""
    async with serve(settings, container) as http:
        response = await http.get("/api/v1/devices")

    assert response.status_code == 401


async def test_the_kill_switch_is_exempt_from_the_rate_limit_and_not_from_the_token(
    settings, container, with_token
):
    """Two different exemptions, and conflating them is the easy mistake.

    §134 exempts the emergency stop from rate limiting so an attacker cannot hold it shut by
    making requests. That is not a reason to let anybody press it — an unauthenticated kill
    switch is a denial-of-service tool with a friendly name.
    """
    from thursday_api.limits import NEVER_LIMITED

    assert any(path.startswith("/api/v1/emergency") for path in NEVER_LIMITED)

    async with serve(settings, container) as http:
        response = await http.post("/api/v1/emergency/stop", json={"scope": "all"})

    assert response.status_code == 401
    # And the stop did not happen. The refusal is before the handler, not a message after it —
    # a kill switch that fires and then reports 401 would be the worst of both.
    assert container.permissions.lockdown is False


# ------------------------------------------------------- what answers without the owner


async def test_a_node_can_still_pair_and_follow_a_key_change_without_the_owners_token(
    settings, container, with_token
):
    """Both exemptions exist because **a node is not the owner and cannot hold the owner's
    token**. `pair/start` proves possession of the node's own key and yields a code a person
    must confirm; `tls-handover` is read by a node that could not open the channel it would
    authenticate on, and the document defends itself (ADR 0071).
    """
    async with serve(settings, container, peer="203.0.113.5", host="thursday.example") as http:
        handover = await http.get("/api/v1/devices/tls-handover")
        pairing = await http.post("/api/v1/devices/pair/start", json={})

    assert handover.status_code == 200
    # 422 is the body being wrong, which is the endpoint answering rather than refusing.
    assert pairing.status_code == 422


def test_the_exemptions_are_exact_paths_so_a_new_route_is_authenticated_by_default():
    """The rate limiter uses prefixes so a new route is *limited* by default. The safe
    direction here is the opposite one, so joining this list has to be deliberate."""
    assert (
        admit(
            path="/api/v1/devices/pair/startle",
            peer="203.0.113.5",
            host_header="thursday.example",
            authorization=None,
            token=TOKEN,
        )
        is not None
    )
    assert len(UNAUTHENTICATED) == 2, "an exemption was added; it needs its own argument"


# --------------------------------------------------------------------- configuration


def test_a_proxy_in_front_of_a_tokenless_deployment_fails_at_startup(settings):
    """The shape where the control silently stops working.

    Loopback-only decides by the immediate peer, and every request through a proxy on this
    machine arrives from 127.0.0.1 — so the whole internet would look like the owner at the
    keyboard. Worse than either setting alone, so it fails where somebody is looking.
    """
    proxied = settings.model_copy(update={"trusted_proxies": ["127.0.0.1"]})

    with pytest.raises(ConfigurationError, match="proxy"):
        api_token(proxied)


def test_requiring_a_token_that_is_not_set_fails_at_startup(settings):
    """For a deployment meant to be reachable, where quietly falling back to loopback-only
    would look like a network fault for as long as anybody cared to debug it."""
    with pytest.raises(ConfigurationError, match="generates"):
        api_token(settings.model_copy(update={"require_api_token": True}))


def test_nothing_here_generates_a_token():
    """Same rule as the enrolment token (ADR 0066), asserted the same way. A secret this code
    invents is one it has to store, print or transmit, and each is a place it leaks from."""
    import inspect

    from thursday_core import auth

    source = inspect.getsource(auth)
    for forbidden in ("secrets.", "token_urlsafe", "token_hex", "uuid4", "random"):
        assert forbidden not in source, f"{forbidden} appears in the auth module"


# ------------------------------------------------------------------------- the parsing


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),  # clients disagree about the case, and a 401 over it is an hour
        ("BEARER  abc  ", "abc"),
        ("Basic abc", None),
        ("abc", None),
        ("Bearer ", None),
        ("", None),
        (None, None),
    ],
)
def test_the_authorization_header_is_read_the_way_clients_actually_send_it(header, expected):
    assert bearer(header) == expected


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("127.0.0.1", True),
        ("127.0.0.1:8000", True),
        ("localhost:8000", True),
        ("LOCALHOST", True),
        ("[::1]:8000", True),  # the brackets keep the address from being split on its colons
        ("[::1]", True),
        ("evil.example", False),
        ("evil.example:8000", False),
        ("127.0.0.1.evil.example", False),
        ("", False),
        (None, False),
    ],
)
def test_the_host_header_is_read_for_what_a_browser_would_actually_send(host, loopback):
    assert host_is_loopback(host) is loopback


def test_a_refusal_says_nothing_useful_to_the_caller_and_everything_to_the_owner():
    """The remedy names an environment variable, which is the owner's business and not the
    caller's. It travels in `note`, which the middleware logs and never sends."""
    refusal = admit(
        path="/api/v1/devices",
        peer="203.0.113.5",
        host_header="thursday.example",
        authorization=None,
        token="",
    )

    assert isinstance(refusal, Refusal)
    assert "THURSDAY_SECRET_API_TOKEN" in refusal.note
    assert "THURSDAY" not in refusal.message


# ------------------------------------------------- the channel the middleware cannot see


def ws_client(settings, container, *, peer: str = "127.0.0.1", host: str = "127.0.0.1:8000"):
    from starlette.testclient import TestClient

    app = create_app(settings, container=container)
    app.state.container = container
    return TestClient(
        app,
        base_url=f"http://{host}",
        client=(peer, 50000),
        headers={"host": host},
    )


def test_the_realtime_socket_is_not_a_way_round_the_token(settings, container, with_token):
    """`@app.middleware("http")` does not run for a WebSocket — and this socket takes
    `type: "turn"` straight into the reasoning engine.

    An HTTP surface that demands the owner's token while the channel that can *ask Thursday to
    do things* takes anyone would be the same hole moved sideways. It is the gap this sprint
    nearly shipped: the REST tests were all green before anybody opened the socket.
    """
    from starlette.websockets import WebSocketDisconnect

    with (
        ws_client(settings, container, peer="203.0.113.5", host="thursday.example") as http,
        pytest.raises(WebSocketDisconnect) as refused,
        http.websocket_connect("/api/v1/realtime"),
    ):
        pass

    # 1008 is "policy violation", and the close happens before `accept` — so nothing was ever
    # sent to a caller that did not prove who it was.
    assert refused.value.code == 1008


def test_a_browser_carries_the_token_in_the_one_place_it_can(settings, container, with_token):
    """The WebSocket API has no way to set a header, and the subprotocol list is the one thing
    it *can* put in the handshake. The server has to echo the accepted subprotocol back or the
    handshake fails, which is why this is checked rather than assumed."""
    from thursday_core.auth import WS_TOKEN_PREFIX

    with (
        ws_client(settings, container) as http,
        http.websocket_connect(
            "/api/v1/realtime", subprotocols=[f"{WS_TOKEN_PREFIX}{TOKEN}"]
        ) as ws,
    ):
        assert ws.receive_json()["type"] == "ready"


def test_a_wrong_token_on_the_socket_is_refused_like_a_wrong_one_on_a_request(
    settings, container, with_token
):
    from starlette.websockets import WebSocketDisconnect
    from thursday_core.auth import WS_TOKEN_PREFIX

    with (
        ws_client(settings, container) as http,
        pytest.raises(WebSocketDisconnect),
        http.websocket_connect("/api/v1/realtime", subprotocols=[f"{WS_TOKEN_PREFIX}wrong"]),
    ):
        pass


def test_the_owner_at_the_keyboard_opens_the_socket_with_no_ceremony(settings, container):
    """No token configured, loopback peer, loopback Host — the default deployment, unchanged."""
    with (
        ws_client(settings, container) as http,
        http.websocket_connect("/api/v1/realtime") as ws,
    ):
        assert ws.receive_json()["type"] == "ready"


def test_a_rebound_page_cannot_open_the_socket_either(settings, container):
    """The Host check is not an HTTP-only control; a WebSocket handshake carries a Host too,
    and a rebinding page opens sockets as readily as it makes requests."""
    from starlette.websockets import WebSocketDisconnect

    with (
        ws_client(settings, container, host="evil.example") as http,
        pytest.raises(WebSocketDisconnect),
        http.websocket_connect("/api/v1/realtime"),
    ):
        pass


def test_an_unrelated_subprotocol_is_left_alone():
    """A client may offer several for reasons of its own, and eating one would break it."""
    from thursday_core.auth import subprotocol_token

    assert subprotocol_token("graphql-ws, thursday.token.abc, json") == "abc"
    assert subprotocol_token("graphql-ws, json") is None
    assert subprotocol_token("thursday.token.") is None
    assert subprotocol_token(None) is None
