"""A signed hand-over, so the core's TLS key can change without a visit to every machine (§117).

Every other rotation in this system is closed. Device keys rotate (ADR 0042), the shared
enrolment token rotates (ADR 0066), provider API keys rotate (ADR 0067). The core's TLS key
was the one left, and §23 has said for several sprints why it is the hard one: a node pins
the core's SubjectPublicKeyInfo, learned at pairing while a person was standing there
(ADR 0041), and it refuses anything else. Replace that key and every node stops connecting at
the same moment — each one needing somebody to walk to it and pair it again.

**The pin is a commitment to a public key, and that is the whole mechanism.** A node does not
store the core's key; it stores `sha256(SPKI)`. But a hand-over that *carries* the retiring
SPKI can be checked against that hash, and once the hash matches, the node holds the real
public key and can verify a signature made by its private half. So a node can be told "the
key you trust says to trust this other key next" and check the claim against nothing but the
pin it already had. No new secret, no new trust anchor, no re-pairing.

**Following it costs no new exposure.** Whoever holds the retiring private key could
impersonate the core to a node pinned to it anyway — that is what a pin means. A hand-over
signed by that key lets them redirect the node instead, which is the same power reached a
different way. That equivalence is what makes this sound, and it is also the sharp edge:
see `sign` for what it refuses because of it.

**There is no window, and that is a difference worth naming.** The enrolment token needed one
(ADR 0066) because two parties had to agree on *when*. Here the statement is a permanent
fact — this key handed over to that one — so a node follows it whenever it happens to notice,
which is the first time it fails to connect. A node in a bag for six months and two rotations
walks the chain when it comes back out. Nothing expires, so nothing bricks a machine that was
switched off at the wrong moment.

**Thursday does not hold the core's TLS key.** It is not generated here, not stored here and
not installed here: the core is served behind whatever terminates TLS for it, and the key is
the operator's. Nothing in this module rotates anything. It signs a statement about a
rotation the operator performs, and teaches nodes to follow it.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from thursday_core.logging import get_logger

from thursday_security.pinning import pin_for_spki

log = get_logger(__name__)

#: Domain prefix. A signature made for one purpose must not verify for another, which is the
#: same reason `hello_payload` and `pairing_payload` differ by their first field.
STATEMENT = "thursday.tls-handover.v1"

#: How many links a node will walk in one go. A bound rather than a limit anyone should reach:
#: a chain arrives from an unauthenticated endpoint, and an unbounded walk is work an
#: unauthenticated caller gets to choose the size of.
CHAIN_MAX = 16


class HandOverRefused(Exception):
    """Base for every reason a node will not follow a hand-over."""


class HandOverNotMine(HandOverRefused):
    """The statement was signed by a key this node never pinned.

    Not an accusation. A node that has already followed this link, or one belonging to a
    different core entirely, lands here — so the caller may reasonably keep looking.
    """


class HandOverInvalid(HandOverRefused):
    """The signature does not verify, or the statement is malformed.

    `keys.py` deliberately makes its verification failures indistinguishable, because there
    the verifier is answering a caller who has not yet proved anything. Here the verifier is
    the owner's own node reading a public document, and the difference between *this is for
    another core* and *this is forged* is something the owner should be told.
    """


class UnsupportedKey(HandOverRefused):
    """The key is of a kind this code cannot sign or verify with."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def _spki(public_key: Any) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _sign_bytes(private_key: Any, message: bytes) -> bytes:
    """Sign with whatever kind of key the operator's certificate happens to use.

    This is the one place in the repository that does not get to choose its algorithm. Device
    identities are Ed25519 because we picked it; a TLS key is whatever the operator's CA,
    proxy or hosting gave them, and refusing RSA would mean refusing most real deployments.

    RSA signs with **PSS**, not PKCS#1 v1.5. Both ends of this are ours, so there is no
    interoperability reason to take the older padding, and PSS is the one with a security
    proof.
    """
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return private_key.sign(message)
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return private_key.sign(message, ec.ECDSA(hashes.SHA256()))
    if isinstance(private_key, rsa.RSAPrivateKey):
        return private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    raise UnsupportedKey(
        f"cannot sign with a {type(private_key).__name__}; "
        "Thursday signs a hand-over with RSA, ECDSA or Ed25519"
    )


def _verify_bytes(public_key: Any, signature: bytes, message: bytes) -> None:
    """Raise unless this key signed this exact message. Mirrors `_sign_bytes` exactly."""
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        public_key.verify(signature, message)
        return
    if isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        return
    if isinstance(public_key, rsa.RSAPublicKey):
        public_key.verify(
            signature,
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return
    raise UnsupportedKey(f"cannot verify a signature by a {type(public_key).__name__}")


@dataclass(frozen=True)
class HandOver:
    """One link: the key with `retiring_spki` says the next key is the one pinned `next_pin`.

    Three fields are signed and all three are load-bearing. `retiring_spki` is what lets a
    holder of nothing but a pin check who signed. `next_pin` is inside the signature so a
    captured signature cannot be re-pointed at a successor of someone else's choosing — the
    lesson `rotation_payload` records about `new_public_key`. `issued_at` orders a chain and
    gives the owner a date to recognise.

    There is deliberately **no hostname and no expiry**. A field inside a signature that the
    verifier does not act on is decoration, and decoration in a signed payload is the kind of
    thing a later reader mistakes for a check.
    """

    retiring_spki: str
    next_pin: str
    issued_at: datetime
    signature: str

    @property
    def retiring_pin(self) -> str:
        """The pin a node would hold for the key that signed this."""
        return pin_for_spki(_unb64(self.retiring_spki))

    def statement(self) -> bytes:
        return "|".join(
            [STATEMENT, self.retiring_spki, self.next_pin, self.issued_at.isoformat()]
        ).encode("utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "retiring_spki": self.retiring_spki,
            "retiring_pin": self.retiring_pin,
            "next_pin": self.next_pin,
            "issued_at": self.issued_at.isoformat(),
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HandOver:
        """Parse without trusting. `retiring_pin` in the input is ignored — it is derived.

        A pin carried alongside the key it is supposed to be a hash of is a field an attacker
        would love the parser to believe. It is in `to_dict` for a human reading the file and
        nowhere in the verification path.
        """
        try:
            return cls(
                retiring_spki=str(raw["retiring_spki"]),
                next_pin=str(raw["next_pin"]),
                issued_at=datetime.fromisoformat(str(raw["issued_at"])),
                signature=str(raw["signature"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HandOverInvalid(f"not a hand-over: {exc}") from exc


def sign(
    private_key_pem: str,
    *,
    next_pin: str,
    issued_at: datetime | None = None,
    compromised: bool,
) -> HandOver:
    """Sign a hand-over with the retiring TLS private key.

    The retiring SPKI is taken from the key being signed with and is **not** a parameter. A
    hand-over naming a key the signer does not hold is precisely the forgery this exists to
    detect, and offering it as an argument would make producing one an ordinary call.

    `compromised` has no keyword default, so the caller has to answer it, and answering
    `True` refuses:

    Following a hand-over is safe because it grants no power the retiring key did not already
    have — see the module docstring. Turn that around and it says what a hand-over cannot do.
    If the retiring key is in someone else's hands, they can sign a hand-over of their own,
    and a node that has not yet followed the real one will follow whichever reaches it first.
    Rotating away from a stolen key is a race, and the nodes that lose it are pointed at the
    attacker by their own pin.

    So there is no honest hand-over for a compromised key, and producing one would leave the
    owner believing the machines had been moved to safety when half of them might not have
    been. The only thing that actually closes it is pairing each node again with a person at
    the machine, which is the same anchor the pin was taken from in the first place.
    """
    if compromised:
        raise HandOverRefused(
            "refusing to sign a hand-over for a key that was compromised. A hand-over is "
            "only trustworthy because the retiring key is trustworthy; whoever holds that "
            "key can sign one too, and each node follows whichever reaches it first. "
            "Re-pair every node with a person at the machine instead — that is the only "
            "step that removes the old key's authority rather than racing it."
        )

    loaded = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    _unb64(next_pin)  # a malformed pin should fail here, not on a node six months from now

    spki = _b64(_spki(loaded.public_key()))
    link = HandOver(
        retiring_spki=spki,
        next_pin=next_pin,
        issued_at=issued_at or datetime.now(UTC),
        signature="",
    )
    signature = _b64(_sign_bytes(loaded, link.statement()))
    signed = HandOver(
        retiring_spki=spki,
        next_pin=next_pin,
        issued_at=link.issued_at,
        signature=signature,
    )
    log.info("tls_handover_signed", retiring=signed.retiring_pin[:8], next=next_pin[:8])
    return signed


def verify(handover: HandOver, pin: str) -> None:
    """Raise unless this hand-over was signed by the key `pin` commits to.

    Raises rather than returning a verdict, for the reason `check_peer` does: there is one
    thing a caller may do with a failed check, and a boolean invites the caller who forgets
    to read it.
    """
    import hmac

    if not hmac.compare_digest(handover.retiring_pin, pin):
        raise HandOverNotMine(
            "this hand-over was signed by a different key from the one this node pinned"
        )

    try:
        public_key = serialization.load_der_public_key(_unb64(handover.retiring_spki))
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise HandOverInvalid(f"the retiring key in this hand-over will not parse: {exc}") from exc

    try:
        _verify_bytes(public_key, _unb64(handover.signature), handover.statement())
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise HandOverInvalid(
            "the signature on this hand-over is not valid for the key it names. Either it "
            "was altered in transit, or somebody produced it without the core's key."
        ) from exc


def follow(chain: list[HandOver], pin: str) -> list[HandOver]:
    """The links this node should walk, starting from the one signed by the key it pinned.

    Returns them in order; the caller's new pin is the last one's `next_pin`. An empty result
    is impossible — a chain with nothing to follow raises, because "verified, and nothing
    changed" would be indistinguishable from "verified, and here is the new key" to a caller
    reading a length.

    A node that missed several rotations starts partway down, which is the case this is for.
    A node whose pin appears nowhere is told so rather than walked from the top: starting at
    the top would mean accepting a chain whose first link this node never trusted.
    """
    if len(chain) > CHAIN_MAX:
        raise HandOverInvalid(
            f"a hand-over chain of {len(chain)} links is longer than the {CHAIN_MAX} this "
            "node will walk"
        )

    start = next((i for i, link in enumerate(chain) if link.retiring_pin == pin), None)
    if start is None:
        raise HandOverNotMine(
            "none of the published hand-overs was signed by the key this node pinned, so "
            "there is no signed path from the key it trusts to the one the core is using"
        )

    walked: list[HandOver] = []
    seen = {pin}
    current = pin
    for link in chain[start:]:
        verify(link, current)
        if link.next_pin in seen:
            raise HandOverInvalid("this hand-over chain loops back to a key already in it")
        walked.append(link)
        seen.add(link.next_pin)
        current = link.next_pin
    return walked
