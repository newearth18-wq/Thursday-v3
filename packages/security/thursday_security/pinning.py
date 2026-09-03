"""Certificate pinning for the device channel (§84, threat T13).

Sprint 36 gave every node an Ed25519 identity and made the core check it. That authentication
runs in **one direction**: the node proves who it is, and the core proves nothing at all.

The consequence is worth stating plainly, because it is not obvious from either end. A node is
the component that runs commands on the owner's real machine. Whoever it believes is the core
gets to drive it. An attacker who can obtain a certificate for the core's hostname — a
mis-issuing CA, a corporate middlebox, a compromised registrar — can stand between them,
accept the node's HELLO, and then send it whatever the node is capable of doing. The node's
own key does not help: it authenticates the node *to* the impostor.

TLS with the public CA set does not close this, because the public CA set is exactly the thing
being assumed. Pinning does: the node remembers which key the core actually had and refuses
anything else, whatever a CA says.

**The SubjectPublicKeyInfo is pinned, not the certificate.** A certificate rotates on renewal
and the key underneath it usually does not, so pinning the certificate breaks the connection
every ninety days on a Let's Encrypt deployment while pinning the SPKI survives renewal with
the same key. Pinning the wrong one is how pinning gets switched off.

**The pin is learned at pairing.** Trust-on-first-use is only as good as the moment it
happens, and pairing is the one moment in this system where a person is standing at the device
confirming a code. That is a far better anchor than a blind first connection, and it costs
nothing extra because the human step is already there.

**A recorded pin cannot be silently dropped.** A node that has one refuses to connect without
TLS, and refuses a TLS connection that does not match. Falling back would mean the pin bought
nothing — the same reasoning that stops a paired device authenticating with the shared token
(ADR 0029).
"""

from __future__ import annotations

import base64
import hashlib
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: How long to wait when fetching a pin from a host that may not be there.
TIMEOUT_S = 10.0


class PinMismatch(Exception):
    """The peer presented a key this node has not agreed to trust."""


class PinUnavailable(Exception):
    """A pin could not be read from the peer. Not the same as a mismatch."""


def spki_pin(certificate_der: bytes) -> str:
    """The standard pin: base64 of the SHA-256 of the SubjectPublicKeyInfo.

    The same value `openssl x509 -pubkey | openssl pkey -pubin -outform der | openssl dgst
    -sha256 -binary | base64` produces, so an operator can check a pin by hand rather than
    trusting this function's word for it.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509 import load_der_x509_certificate

    certificate = load_der_x509_certificate(certificate_der)
    spki = certificate.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(spki).digest()).decode("ascii")


@dataclass(frozen=True)
class Pin:
    """What a node remembers about the core it paired with."""

    value: str
    host: str = ""

    #: A short form for showing the owner, so "is this the same core" is answerable by eye.
    @property
    def short(self) -> str:
        return f"{self.value[:8]}…{self.value[-6:]}"

    def matches(self, other: str) -> bool:
        # compare_digest: a pin is not secret, but a comparison that returns early leaks how
        # much of a guess was right, and there is no reason to write the leaky version.
        import hmac

        return hmac.compare_digest(self.value, other)


def peer_pin(url: str, *, timeout: float = TIMEOUT_S) -> str:
    """Connect to a host and read the pin of the certificate it presents.

    Deliberately does **not** validate the chain. This runs at pairing, where the point is to
    record what the core actually has rather than to ask a CA whether it approves — and on a
    home network the core very often has a self-signed certificate, which is precisely the
    case pinning exists to make safe.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise PinUnavailable(f"{url!r} has no host to connect to")
    port = parts.port or (443 if parts.scheme in {"https", "wss"} else 80)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            certificate = tls.getpeercert(binary_form=True)
    except OSError as exc:
        raise PinUnavailable(
            f"could not reach {host}:{port} to read its certificate: {exc}"
        ) from exc

    if not certificate:
        raise PinUnavailable(f"{host}:{port} presented no certificate")
    return spki_pin(certificate)


def check_peer(ssl_object: ssl.SSLObject | ssl.SSLSocket | None, expected: Pin) -> None:
    """Verify a live connection against the recorded pin. Raises, never returns a verdict.

    Raising rather than returning a boolean because there is exactly one thing a caller may
    do with a failed pin check, and a boolean invites the caller who forgets to look at it.

    A `None` ssl object means the connection is not TLS at all. That is a mismatch, not a
    special case: a node holding a pin that connects in the clear has had its pin removed by
    whoever chose the URL.
    """
    if ssl_object is None:
        raise PinMismatch(
            "this node pinned the core's certificate and the connection is not encrypted; "
            "refusing to hand a plaintext channel the commands it can run"
        )

    certificate = ssl_object.getpeercert(binary_form=True)
    if not certificate:
        raise PinMismatch("the core presented no certificate")

    actual = spki_pin(certificate)
    if not expected.matches(actual):
        log.error("core_pin_mismatch", expected=expected.short, actual=f"{actual[:8]}…")
        raise PinMismatch(
            "the core is presenting a different key from the one this node paired with. "
            "Either the core's certificate was replaced, or something is between them. "
            "Re-pair the node deliberately if the change was expected."
        )


def pinned_context(*, trust_self_signed: bool = True) -> ssl.SSLContext:
    """A TLS context for a connection whose trust comes from the pin, not from a CA.

    Chain validation is turned off *because the pin replaces it*, and that is only sound
    because `check_peer` is called on every connection afterwards. A context built here and
    then not checked is strictly worse than the default one — it is the shape of a real
    mistake, so the caller that builds it must be the caller that checks it.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if trust_self_signed:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context
