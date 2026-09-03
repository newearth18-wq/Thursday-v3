"""Rate limiting on the HTTP surface (§128) — Sprint 53.

§128 names five surfaces to limit. Thursday has no login (§23.2 — single owner, no user
model), and pairing already limits itself at the service layer, where the interesting budget
lives. This is the layer in front, and it exists for a narrower threat than a stranger on the
internet: something on the machine or the LAN — a runaway retry loop, a curious process —
turning an endpoint that costs a model call into an unbounded bill.

Two failures would be worse than having no limiter at all, and most of this file is about
them. One is throttling the owner's own client mid-conversation. The other is keying the
bucket on something the caller picks, which is a limiter with an off switch.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from thursday_api.app import EXPENSIVE_PREFIXES, create_app
from thursday_api.limits import Limit, RateLimiter, caller_of, classify
from thursday_core.container import build_container


def app_with(settings, **limits):
    """An app whose limits are low enough to reach in a test."""
    tightened = settings.model_copy(update=limits)
    return create_app(tightened, container=build_container(tightened, configure_logs=False))


# --------------------------------------------------------------------------- the counter


def test_a_window_that_slides_rather_than_resetting():
    """A fixed window lets a caller spend the whole budget in its last instant and the whole
    budget again in the first instant of the next — twice the limit, at exactly the moment
    somebody is trying to exceed it."""
    now = [0.0]
    limiter = RateLimiter({"x": Limit(2, 60.0)}, clock=lambda: now[0])

    now[0] = 59.0
    assert [limiter.check("a", "x").allowed for _ in range(3)] == [True, True, False]

    # One second later a fixed window would have reset and allowed two more.
    now[0] = 60.5
    assert limiter.check("a", "x").allowed is False

    # The slot frees when the *oldest* request leaves the window, not on a boundary.
    now[0] = 119.5
    assert limiter.check("a", "x").allowed is True


def test_the_wait_it_reports_is_the_wait_that_works():
    """A client that waits exactly as long as it was told must then succeed. If the number is
    short the client comes back too early and stays limited; the obvious wrong answer is to
    report the whole window."""
    now = [100.0]
    limiter = RateLimiter({"x": Limit(1, 60.0)}, clock=lambda: now[0])
    limiter.check("a", "x")

    now[0] = 130.0
    refused = limiter.check("a", "x")
    assert refused.allowed is False
    assert refused.retry_after_s == pytest.approx(30.0)

    now[0] += refused.retry_after_s
    assert limiter.check("a", "x").allowed is True


def test_callers_do_not_share_a_budget():
    limiter = RateLimiter({"x": Limit(1, 60.0)})
    assert limiter.check("first", "x").allowed is True
    assert limiter.check("second", "x").allowed is True
    assert limiter.check("first", "x").allowed is False


def test_classes_do_not_share_a_budget():
    """Reading the device list all day must not use up the budget for asking a question."""
    limiter = RateLimiter({"default": Limit(1, 60.0), "expensive": Limit(1, 60.0)})
    assert limiter.check("a", "default").allowed is True
    assert limiter.check("a", "expensive").allowed is True


def test_the_bucket_map_is_bounded():
    """An unbounded map keyed on something a caller varies is a slow leak an attacker can
    drive on purpose."""
    limiter = RateLimiter({"x": Limit(5, 60.0)}, max_buckets=8)
    for i in range(200):
        limiter.check(f"caller-{i}", "x")
    assert limiter.buckets <= 8


def test_an_unknown_class_is_not_limited_rather_than_limited_to_nothing():
    """Fail *open* here, deliberately and unusually. A class with no configured limit is a
    routing mistake, and answering it with 429 would take an endpoint offline over a typo in
    a settings file."""
    assert RateLimiter({}).check("a", "nonexistent").allowed is True


def test_a_limit_that_allows_nothing_is_refused_at_construction():
    with pytest.raises(ValueError, match="broken endpoint"):
        Limit(0, 60.0)


# --------------------------------------------------------------------------- the key


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored():
    """The trap this exists to avoid. `X-Forwarded-For` is written by the caller, so a
    limiter that believes it lets an attacker pick a fresh bucket per request — which is not
    a weaker limit, it is no limit."""
    assert caller_of("10.0.0.5", "1.2.3.4", frozenset()) == "10.0.0.5"
    assert caller_of("10.0.0.5", "1.2.3.4", frozenset({"10.0.0.9"})) == "10.0.0.5"


def test_a_forwarded_header_from_a_configured_proxy_is_believed():
    """§127 puts a reverse proxy in front. Without this every request behind it shares one
    bucket — which is the honest default, and something a deployment opts out of by naming
    its proxy rather than by trusting whatever arrives."""
    assert caller_of("10.0.0.9", "1.2.3.4", frozenset({"10.0.0.9"})) == "1.2.3.4"
    # Only the left-most entry, which is the original client; later hops added themselves.
    assert caller_of("10.0.0.9", "1.2.3.4, 10.0.0.9", frozenset({"10.0.0.9"})) == "1.2.3.4"


def test_a_peerless_request_still_gets_a_bucket():
    """`request.client` can be None. Falling through to "no key" would mean no limit."""
    assert caller_of(None, None, frozenset()) == "unknown"
    assert caller_of("10.0.0.9", "   ", frozenset({"10.0.0.9"})) == "10.0.0.9"


# --------------------------------------------------------------------------- what is exempt


@pytest.mark.parametrize(
    "path",
    ["/api/v1/emergency/stop", "/api/v1/emergency/release", "/api/v1/health", "/health"],
)
def test_the_kill_switch_and_the_health_checks_are_never_limited(path):
    """§134. A kill switch that can be rate-limited is one an attacker holds shut by making
    requests, and every second it is held is a second Thursday keeps acting."""
    assert (
        classify(path, "POST", expensive=EXPENSIVE_PREFIXES, approvals="/api/v1/approvals") is None
    )


def test_the_emergency_stop_still_answers_a_caller_who_is_over_every_budget(settings):
    """Not a restatement of the classifier: this drives it through the app, because the
    exemption is worth nothing if the middleware never consults it."""
    app = app_with(settings, rate_limit_default_per_minute=1, rate_limit_expensive_per_minute=1)
    with TestClient(app) as client:
        assert client.get("/api/v1/agents").status_code == 200
        assert client.get("/api/v1/agents").status_code == 429

        stop = client.post("/api/v1/emergency/stop", json={"scope": "all"})
        assert stop.status_code == 200, "the kill switch must not be refused"
        assert client.get("/api/v1/health").status_code == 200


def test_the_device_socket_is_not_touched_by_the_limiter(settings):
    """HTTP middleware does not see a WebSocket, and a node reconnecting after a session
    expiry must never be answered with a 429 it has no way to read."""
    app = app_with(settings, rate_limit_default_per_minute=1)
    with TestClient(app) as client:
        client.get("/api/v1/agents")
        assert client.get("/api/v1/agents").status_code == 429

        with client.websocket_connect("/api/v1/device") as ws:
            ws.send_text(json.dumps({"v": 1, "type": "HEARTBEAT", "telemetry": {}}))
            frame = json.loads(ws.receive_text())
    assert frame["type"] == "ERROR"
    assert frame["code"] == "protocol_error", "refused by the protocol, not by the limiter"


# --------------------------------------------------------------------------- through the app


def test_an_expensive_route_is_limited_before_a_cheap_one(settings):
    """The point of separate classes. Asking questions is what costs money; listing agents
    is not, and the two must not share a budget."""
    app = app_with(settings, rate_limit_expensive_per_minute=2, rate_limit_default_per_minute=100)
    with TestClient(app) as client:
        codes = [
            client.post("/api/v1/conversations", json={"text": "hello"}).status_code
            for _ in range(3)
        ]
        assert codes[-1] == 429, codes
        assert client.get("/api/v1/agents").status_code == 200


def test_the_refusal_follows_the_error_format_and_says_how_long_to_wait(settings):
    """§48, and the header a well-behaved client needs. Without `Retry-After` a caller backs
    off by guessing, and the common guess — retry immediately — keeps the limit tripped."""
    app = app_with(settings, rate_limit_default_per_minute=1)
    with TestClient(app) as client:
        client.get("/api/v1/agents")
        refused = client.get("/api/v1/agents", headers={"x-trace-id": "caller-chose-this"})

    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["details"]["retry_after_s"] > 0
    assert int(refused.headers["Retry-After"]) >= 1

    # The caller's own trace id, in both places. The first version of this middleware ran
    # outside the trace middleware, so a 429 came back with no `x-trace-id` header and a
    # freshly minted id in its body — an error nobody could correlate with its request.
    assert refused.headers["x-trace-id"] == "caller-chose-this"
    assert body["trace_id"] == "caller-chose-this"


def test_pairing_keeps_its_own_budget_underneath_the_http_limit(settings):
    """Two layers doing different jobs. The HTTP limit stops requests arriving that fast; the
    service's guess budget stops a six-digit code being brute-forced, and it counts across
    *all* codes because the ones an attacker guesses do not exist."""
    app = app_with(settings, rate_limit_pairing_per_minute=100)
    with TestClient(app) as client:
        codes = [
            client.post("/api/v1/devices/pair/complete", json={"code": f"{n:06d}"}).status_code
            for n in range(12)
        ]
    assert 429 not in codes, "the HTTP limit was set high enough not to be what refused these"
    assert 400 in codes or 403 in codes, codes
