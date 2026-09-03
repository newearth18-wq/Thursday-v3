"""Ed25519 keys for device identity (§82, Sprint 36).

A thin wrapper over `cryptography`, and thin on purpose: the value here is not the algorithm
— that is somebody else's well-reviewed code — but the *shape* of the API, which is built so
that the dangerous mistakes are hard to make.

Three of them, specifically:

**A private key cannot be sent to the Core.** `PublicKey` and `PrivateKey` are separate
types, and only the public half has a serialisation the API layer accepts. The Core stores
public keys; a device that hands over its private key has stopped being an identity and
become a shared secret with extra steps.

**A signature over the wrong thing is not a valid signature.** The payload is built by
`signing_payload` from named fields rather than assembled at each call site. Two components
that agree on a signature scheme but disagree on field order silently accept anything.

**Verification failures are indistinguishable.** A wrong key, a corrupted signature and a
tampered payload all return `False` with no detail. Telling an unauthenticated caller *which*
check failed helps only an attacker.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

#: Ed25519 rather than RSA or ECDSA. Small keys, small signatures, no parameter choices to
#: get wrong, and no nonce to reuse — ECDSA's catastrophic failure mode is a repeated nonce,
#: and Ed25519 has no way to reach it.
ALGORITHM = "ed25519"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


@dataclass(frozen=True)
class PublicKey:
    """The half the Core keeps. Safe to store, log and transmit."""

    encoded: str

    @classmethod
    def from_raw(cls, key: Ed25519PublicKey) -> PublicKey:
        return cls(
            encoded=_b64(
                key.public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            )
        )

    def _key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(_unb64(self.encoded))

    def verify(self, payload: str, signature: str) -> bool:
        """Whether this key signed this exact payload.

        Every failure returns `False` with no explanation. A caller who has not yet proved
        who they are does not get told which of their inputs was wrong.
        """
        try:
            self._key().verify(_unb64(signature), payload.encode("utf-8"))
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True

    #: A short, stable, human-comparable form. For showing the owner which key a device
    #: paired with, so "is that the same device" is answerable without reading base64.
    @property
    def fingerprint(self) -> str:
        import hashlib

        digest = hashlib.sha256(_unb64(self.encoded)).hexdigest()
        return ":".join(digest[i : i + 4] for i in range(0, 16, 4))


@dataclass(frozen=True)
class PrivateKey:
    """The half that never leaves the device.

    Deliberately has no `.encoded` property matching `PublicKey`'s: exporting it is
    `to_pem`, which reads like the serious act it is, and there is nothing on this class the
    API layer will accept.
    """

    _key: Ed25519PrivateKey

    @classmethod
    def generate(cls) -> PrivateKey:
        return cls(_key=Ed25519PrivateKey.generate())

    @classmethod
    def from_pem(cls, pem: str) -> PrivateKey:
        loaded = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError("not an Ed25519 private key")
        return cls(_key=loaded)

    def to_pem(self) -> str:
        """For writing to the node's own key file, with the file mode the caller's job."""
        return self._key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    @property
    def public(self) -> PublicKey:
        return PublicKey.from_raw(self._key.public_key())

    def sign(self, payload: str) -> str:
        return _b64(self._key.sign(payload.encode("utf-8")))


def generate_keypair() -> tuple[PrivateKey, PublicKey]:
    private = PrivateKey.generate()
    return private, private.public


def pairing_payload(
    *,
    public_key: str,
    name: str,
    os: str,
    hostname: str,
    nonce: str,
    issued_at: datetime,
) -> str:
    """What a node signs to prove it holds the key it is offering.

    Built from named fields in one place rather than assembled at each call site. Two
    components that agree on a signature scheme but disagree about field order produce a
    verifier that accepts anything, and the bug is invisible until somebody looks for it.

    The public key is *inside* the payload, so a signature cannot be lifted from one pairing
    request and replayed against a different key.
    """
    return "|".join(
        [
            "thursday.pair.v1",
            public_key,
            name,
            os,
            hostname,
            nonce,
            issued_at.isoformat(),
        ]
    )


def hello_payload(*, device_id: str, name: str, os: str, nonce: str, issued_at: datetime) -> str:
    """What a paired node signs on every connection.

    Distinct from `pairing_payload` and prefixed differently: a signature made for one
    purpose must not be valid for the other. Without the domain prefix, a captured pairing
    signature could be presented as a connection signature.
    """
    return "|".join(["thursday.hello.v1", device_id, name, os, nonce, issued_at.isoformat()])


def rotation_payload(
    *,
    device_id: str,
    old_fingerprint: str,
    new_public_key: str,
    nonce: str,
    issued_at: datetime,
) -> str:
    """What a node signs — twice — to replace its own key (§82, §117).

    Domain-prefixed like the other two, for the same reason: a rotation request must not be
    constructible from a signature made for a connection.

    Two fields here are doing security work that is easy to leave out.

    ``old_fingerprint`` binds the request to the exact credential it replaces. Without it, a
    rotation captured today stays valid against whatever key the device holds next year, so
    an attacker who recorded one request could undo every later rotation with it.

    ``new_public_key`` binds it to the incoming key. Without it, an attacker who intercepted
    a rotation could swap in a key of their own and keep the victim's signature — which is
    the whole attack this exists to prevent.
    """
    return "|".join(
        [
            "thursday.rotate.v1",
            device_id,
            old_fingerprint,
            new_public_key,
            nonce,
            issued_at.isoformat(),
        ]
    )
