"""Who may talk to this API at all (§128, threat T15).

Lives in `thursday_core` rather than beside the middleware that calls it, because the
WebSocket gateway needs the same decision and a lower layer must not import an upper one.
One function, two callers, and no second copy to drift out of agreement with the first.

Sprint 100 rate-limited the HTTP surface and §23 said what that left open, in one sentence
nobody had acted on: *"it is a bound on rate rather than authentication — of which there is
still none."*

What that meant in practice, measured rather than reasoned about: an unauthenticated
`POST /api/v1/devices/{id}/actions` with `app.open` came back **200, ok, verified** — Chrome
opened on the owner's machine. The device channel demands an Ed25519 signature from every node
before it will carry a single frame (ADR 0029). The HTTP API beside it ran the same catalogue
for anybody who could reach the port. **The front door had a cryptographic lock and the window
was open.**

**One owner, so one token.** There is no user model here and §23 is clear that this is a design
position rather than an oversight. A login, sessions and per-user isolation would be answering
a question nobody asked. What was missing is much smaller: proof that the caller is *the owner*
rather than anything else that can open a socket.

**Nothing generates it.** Same rule as the enrolment token (ADR 0066) and for the same reason:
a secret this code invents is a secret this code has to store, print or transmit, and each of
those is a place it leaks from. It is read from the environment, or it is not configured.

**Not configured means loopback only.** That is the honest default rather than an open door:
§23 already told the owner not to expose the API beyond localhost, and this turns that sentence
into a control instead of a hope. A deployment that wants to be reachable from a phone or
another machine sets a token — which is the same act, said once, in the place that enforces it.

**And loopback is not a credential.** A page in the owner's browser can be made to arrive on
127.0.0.1 by resolving its own hostname there — DNS rebinding — and the browser will treat it
as same-origin. So in loopback-only mode the `Host` header has to be a loopback name too. With
a token configured the check is unnecessary and would be wrong: the deployment is deliberately
on some other hostname, and a rebound page still cannot produce the token.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

#: Host values a browser uses for the machine it is running on. A rebinding page arrives with
#: its own hostname here, which is the whole signal.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

#: Peer addresses that are this machine. Addresses only — a test harness's idea of a peer name
#: does not belong in a security check that ships.
LOOPBACK_PEERS: frozenset[str] = frozenset({"127.0.0.1", "::1"})

#: The only paths that answer without the owner's token, and each is here because **a node is
#: not the owner and cannot hold the owner's token**.
#:
#: `pair/start` is a node proving it holds the key it is offering; what it gets back is a code
#: a person has to confirm, so possession alone enrols nothing. `tls-handover` is read by a node
#: that could not open the channel it would authenticate on — that is the entire situation it
#: exists for (ADR 0071) — and the document defends itself, because a chain the core's key did
#: not sign moves nothing.
#:
#: Exact paths rather than prefixes. The rate limiter uses prefixes so a new route is limited by
#: default; the safe direction here is the opposite one, so a new route is **authenticated** by
#: default and joining this list is a deliberate act.
UNAUTHENTICATED: frozenset[str] = frozenset(
    {
        "/api/v1/devices/pair/start",
        "/api/v1/devices/tls-handover",
    }
)


#: How a browser carries the token on a WebSocket. The WebSocket API has no way to set a
#: header, and the subprotocol list is the one thing it *can* put in the handshake — which is
#: why this is the usual answer and not a clever one. It travels in `Sec-WebSocket-Protocol`,
#: so it is exposed exactly as much as `Authorization` would be, and no more; a query string
#: would have put it in every access log on the way.
WS_TOKEN_PREFIX = "thursday.token."  # noqa: S105


@dataclass(frozen=True)
class Refusal:
    """Why a request was not admitted, in the form the middleware will send."""

    status: int
    message: str
    #: What to write in the log for the owner, where naming the remedy is useful. Kept out of
    #: the response: a caller who has not proved who they are is not told how this is secured.
    note: str = ""
    headers: dict[str, str] | None = None


def bearer(authorization: str | None) -> str | None:
    """The token out of an `Authorization: Bearer …` header, or None.

    Case-insensitive on the scheme, because clients disagree about it and a 401 over
    capitalisation is an hour somebody does not get back.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def subprotocol_token(offered: str | None) -> str | None:
    """The token out of a `Sec-WebSocket-Protocol` list, or None.

    Anything that is not the token prefix is left alone: a client may offer several
    subprotocols for unrelated reasons, and eating one of them would break it.
    """
    for entry in (offered or "").split(","):
        value = entry.strip()
        if value.startswith(WS_TOKEN_PREFIX):
            return value[len(WS_TOKEN_PREFIX) :] or None
    return None


def is_loopback(peer: str | None) -> bool:
    return (peer or "") in LOOPBACK_PEERS


def host_is_loopback(host_header: str | None) -> bool:
    """Whether the `Host` header names this machine.

    The port is dropped: `127.0.0.1:8000` and `127.0.0.1` are the same machine, and an IPv6
    literal keeps its brackets so `[::1]:8000` does not lose its address to a naive split.
    """
    host = (host_header or "").strip().lower()
    if not host:
        return False
    if host.startswith("["):
        host = host.partition("]")[0] + "]"
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host in LOOPBACK_HOSTS


def admit(
    *,
    path: str,
    peer: str | None,
    host_header: str | None,
    authorization: str | None,
    token: str,
    subprotocol: str | None = None,
) -> Refusal | None:
    """Whether this request may proceed. `None` admits it; a `Refusal` says why not.

    A plain function over a few strings, so the decision can be read and tested without an
    application, a client or a socket around it — and so the WebSocket gateway reaches the
    same decision as the HTTP middleware rather than a second one written to match.

    `subprotocol` is the already-extracted token from a WebSocket handshake, because a browser
    cannot set a header on one. It is checked in exactly the same place as the header and
    against the same secret; the only difference is how it arrived.
    """
    if not token:
        # Loopback-only mode. Both halves are needed: the peer, so another machine cannot
        # reach it, and the Host, so a page in the owner's own browser cannot be pointed at it.
        if not is_loopback(peer):
            return Refusal(
                status=403,
                message="this Thursday answers only on the machine it runs on",
                note=(
                    "a request arrived from off-machine and no API token is configured. "
                    "Set THURSDAY_SECRET_API_TOKEN to allow callers from elsewhere."
                ),
            )
        if not host_is_loopback(host_header):
            return Refusal(
                status=400,
                message="unexpected Host header",
                note=(
                    f"a loopback request arrived with Host {host_header!r}, which is how DNS "
                    "rebinding reaches a local API from a web page. Refused."
                ),
            )
        return None

    if path in UNAUTHENTICATED:
        return None

    presented = bearer(authorization) or subprotocol
    # compare_digest on both the missing case and the wrong one, and the same sentence for
    # each: which of the two it was is not something an unauthenticated caller gets told.
    if presented is None or not hmac.compare_digest(presented, token):
        return Refusal(
            status=401,
            message="this request needs Thursday's API token",
            note="a request arrived without a usable token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return None
