"""Certificate pinning for the device channel (§84, threat T13, ADR 0041).

Sprint 36 gave every node an Ed25519 identity and made the core check it — in one direction.
The node proves who it is; the core proves nothing. A node is the component that runs commands
on the owner's real machine, so whoever it believes is the core gets to drive it, and an
attacker holding a certificate for the core's hostname is exactly that.

These tests run against a **real TLS handshake** — a local server with a generated certificate
— rather than a mocked socket, because the claim is about what happens on the wire and a
mocked socket would only prove that the mock was written to agree.
"""

from __future__ import annotations

import contextlib
import datetime
import socket
import ssl
import threading
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from thursday_security.pinning import (
    Pin,
    PinMismatch,
    PinUnavailable,
    check_peer,
    peer_pin,
    pinned_context,
    spki_pin,
)


def make_certificate(directory: Path, name: str, *, key: rsa.RSAPrivateKey | None = None):
    """A self-signed certificate, and the pin it should produce.

    `key` is reusable on purpose: renewing a certificate while keeping the key is the case
    that decides whether pinning the SPKI or the certificate was the right choice.
    """
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / f"{name}.pem"
    key_path = directory / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, spki_pin(certificate.public_bytes(serialization.Encoding.DER)), key


class TlsServer:
    """A real TLS listener on localhost. No network leaves this machine."""

    def __init__(self, certfile: Path, keyfile: Path) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        self._context = context
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            with contextlib.suppress(OSError):
                self._context.wrap_socket(connection, server_side=True).close()

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    def close(self) -> None:
        self._socket.close()


@pytest.fixture
def core(tmp_path):
    cert, key, pin, private = make_certificate(tmp_path, "core")
    server = TlsServer(cert, key)
    yield server, pin, private, tmp_path
    server.close()


# --------------------------------------------------------------------------- the pin itself


def test_the_pin_is_read_from_the_certificate_actually_served(core):
    server, expected, _, _ = core
    assert peer_pin(server.url) == expected


def test_a_different_key_produces_a_different_pin(tmp_path):
    _, _, first, _ = make_certificate(tmp_path, "a")
    _, _, second, _ = make_certificate(tmp_path, "b")
    assert first != second


def test_renewing_a_certificate_with_the_same_key_keeps_the_pin(tmp_path):
    """The reason the SubjectPublicKeyInfo is pinned rather than the certificate. Pinning the
    certificate breaks the connection on every renewal, and a pin that breaks routinely is a
    pin somebody switches off."""
    _, _, original, key = make_certificate(tmp_path, "before")
    _, _, renewed, _ = make_certificate(tmp_path, "after", key=key)
    assert renewed == original


def test_the_pin_matches_what_openssl_would_produce(tmp_path):
    """Computed independently rather than by calling the same function twice: a test that
    reuses the implementation proves the implementation agrees with itself."""
    import base64
    import hashlib

    cert_path, _, produced, _ = make_certificate(tmp_path, "check")
    certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert produced == base64.b64encode(hashlib.sha256(spki).digest()).decode()


def test_a_pin_comparison_does_not_return_early():
    import inspect

    from thursday_security import pinning

    assert "compare_digest" in inspect.getsource(pinning.Pin.matches)


# --------------------------------------------------------------------------- on the wire


def connect(url: str, pin: Pin) -> None:
    """Do what the node does: connect with the pinned context, then check the peer."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    context = pinned_context()
    with (
        socket.create_connection((parts.hostname, parts.port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname=parts.hostname) as tls,
    ):
        check_peer(tls, pin)


def test_the_right_core_is_accepted(core):
    server, expected, _, _ = core
    connect(server.url, Pin(value=expected))  # does not raise


def test_a_core_presenting_a_different_key_is_refused(core, tmp_path):
    """The attack this exists for: somebody with a certificate for the core's hostname,
    from any CA the machine happens to trust."""
    server, _, _, _ = core
    _, _, impostor, _ = make_certificate(tmp_path, "impostor")

    with pytest.raises(PinMismatch, match="different key"):
        connect(server.url, Pin(value=impostor))


def test_the_refusal_says_what_to_do_about_it(core, tmp_path):
    server, _, _, _ = core
    _, _, impostor, _ = make_certificate(tmp_path, "impostor2")
    with pytest.raises(PinMismatch) as raised:
        connect(server.url, Pin(value=impostor))
    assert "Re-pair" in str(raised.value)


def test_a_pinned_node_refuses_a_plaintext_connection():
    """A node holding a pin that connects in the clear has had its pin removed by whoever
    chose the URL. Falling back would mean the pin bought nothing (ADR 0029's reasoning)."""
    with pytest.raises(PinMismatch, match="not encrypted"):
        check_peer(None, Pin(value="anything"))


def test_a_host_that_is_not_there_is_unavailable_not_a_mismatch():
    """The difference matters: unreachable is an operational problem, and a mismatch is an
    attack. Collapsing them would train somebody to ignore the second."""
    with pytest.raises(PinUnavailable, match="could not reach"):
        peer_pin("https://127.0.0.1:1", timeout=1.0)


def test_the_pinned_context_is_only_sound_because_the_caller_checks(core):
    """`pinned_context` turns off chain validation *because the pin replaces it*. A context
    built and then not checked is strictly worse than the default one, so this test states
    the obligation the docstring describes."""
    server, expected, _, _ = core
    from urllib.parse import urlsplit

    parts = urlsplit(server.url)
    context = pinned_context()
    assert context.verify_mode is ssl.CERT_NONE

    # Without check_peer, any certificate is accepted — which is what makes check_peer
    # mandatory rather than advisory.
    with (
        socket.create_connection((parts.hostname, parts.port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname=parts.hostname) as tls,
    ):
        assert tls.getpeercert(binary_form=True), "handshake succeeded with no CA involved"
        check_peer(tls, Pin(value=expected))


# --------------------------------------------------------------------------- the node


def identity(tmp_path):
    from thursday_security.keychain import NoKeychain

    from apps.node.__main__ import NodeIdentity

    return NodeIdentity(tmp_path / "node.json", keychain=NoKeychain())


def test_a_node_records_the_pin_with_its_pairing(tmp_path):
    import uuid

    node = identity(tmp_path)
    node.record_pairing(
        device_id=str(uuid.uuid4()), fingerprint=node.fingerprint, core="wss://core", pin="abc123"
    )

    assert node.core_pin is not None
    assert node.core_pin.value == "abc123"
    assert node.core_pin.host == "wss://core"


def test_a_node_that_recorded_no_pin_has_none(tmp_path):
    """A LAN deployment on plain ws:// is a supported configuration; what must not happen is
    a node believing it has a pin when it does not."""
    import uuid

    node = identity(tmp_path)
    node.record_pairing(device_id=str(uuid.uuid4()), fingerprint=node.fingerprint, core="ws://core")
    assert node.core_pin is None


def test_forgetting_a_pairing_forgets_its_pin(tmp_path):
    import uuid

    node = identity(tmp_path)
    node.record_pairing(
        device_id=str(uuid.uuid4()), fingerprint=node.fingerprint, core="wss://core", pin="abc"
    )
    node.forget_pairing()
    assert node.core_pin is None


def test_the_node_checks_the_pin_before_sending_anything(tmp_path):
    """A HELLO handed to an impostor is a HELLO an impostor can relay, and the node's own key
    does not help — it authenticates this node *to* whoever is listening."""
    import inspect

    from apps.node.__main__ import NodeClient

    source = inspect.getsource(NodeClient._session)
    check = source.index("check_peer")
    send = source.index("await ws.send(hello.model_dump_json())")
    assert check < send, "the pin must be verified before the HELLO goes out"
