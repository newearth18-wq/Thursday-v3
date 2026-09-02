"""Device authentication for TNP/1 (§9.1, §80–83, PART 26).

A node is the one component that runs commands on the owner's actual machine and reports
back whether they worked. Both halves matter. An impostor node could act, but worse, it
could *lie*: report ``verified: true`` for something it never did. Verification is the
property the whole system rests on, and it is only worth as much as the identity of the
thing doing the verifying.

So the HELLO frame is signed, and the signature is checked — not merely required to be
present. There are two ways a node can be signing, and which one applies is decided by the
core from its own records rather than by anything the node says:

**A paired device** (Sprint 36) is judged against the Ed25519 public key it registered, and
against nothing else. Once a device has paired, the shared token is closed for it for ever
— otherwise pairing would have improved nothing, since anyone holding the enrolment token
could still connect as that machine. A device the registry knows and has **revoked** fails
here too, rather than dropping through to the token: revocation a shared secret can route
around is not revocation.

**A device that has never paired** falls back to the bootstrap scheme of ADR 0013: one
shared enrolment token from the environment, HMAC-SHA256 over the fields that identify the
node, compared with ``hmac.compare_digest``. It is still here because enrolment has to
start somewhere, and it is now strictly an enrolment path — it authenticates *a* node, not
*this* node, and every device that pairs leaves it behind.

Both paths share the replay defences, because both need them: the frame carries its own
timestamp, checked against the core's clock, and a nonce that is remembered for as long as
a captured frame could still be inside the skew window.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_shared.models import utcnow

from thursday_security.keys import hello_payload

#: How far a HELLO's own timestamp may sit from the core's clock. Wide enough for a laptop
#: whose clock drifted, narrow enough that a captured frame is stale before it is useful.
MAX_CLOCK_SKEW = timedelta(minutes=5)

#: Nonces remembered inside the skew window. Bounded: a node that reconnects in a loop must
#: not be able to grow this without limit.
MAX_REMEMBERED_NONCES = 4096


@dataclass(frozen=True)
class AuthOutcome:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def signing_payload(*, device_id: str, name: str, os: str, nonce: str, issued_at: datetime) -> str:
    """The exact bytes both sides sign.

    Every field a claim depends on is in here. Signing only the nonce would let an attacker
    who captured one HELLO re-present it under a different device name.
    """
    return "|".join([device_id, name, os, nonce, issued_at.isoformat()])


def sign(token: str, payload: str) -> str:
    return hmac.new(token.encode(), payload.encode(), hashlib.sha256).hexdigest()


class DeviceAuthenticator:
    """Checks the HELLO signature. One object, one decision, no side effects on failure."""

    def __init__(
        self,
        token: str | None,
        *,
        required: bool = True,
        pairing: Any = None,
    ) -> None:
        self._token = token
        self.required = required
        self._seen: OrderedDict[str, datetime] = OrderedDict()
        #: The registry of per-device public keys (Sprint 36). When a device has paired,
        #: its own key is the only thing that authenticates it and the shared token stops
        #: working for it — which is the entire point of pairing.
        self._pairing = pairing

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def verify(
        self,
        *,
        device_id: str,
        name: str,
        os: str,
        nonce: str,
        issued_at: datetime,
        signature: str,
        now: datetime | None = None,
    ) -> AuthOutcome:
        now = now or utcnow()

        if not self.required:
            return AuthOutcome(True, "signature checking is disabled for this environment")

        if not self.configured:
            # Fail closed. A deployment that requires signatures but configured no token is
            # misconfigured, and guessing that it meant "allow everything" is how an
            # unauthenticated device ends up trusted in production.
            return AuthOutcome(False, "no device token is configured on the core")

        if not signature:
            return AuthOutcome(False, "the HELLO frame carried no signature")

        skew = abs(now - issued_at)
        if skew > MAX_CLOCK_SKEW:
            return AuthOutcome(
                False, f"HELLO timestamp is {skew.total_seconds():.0f}s from the core's clock"
            )

        keyed = self._verify_with_key(
            device_id=device_id,
            name=name,
            os=os,
            nonce=nonce,
            issued_at=issued_at,
            signature=signature,
        )
        if keyed is not None:
            if not keyed.ok:
                return keyed
        elif not self._verify_with_token(
            device_id=device_id,
            name=name,
            os=os,
            nonce=nonce,
            issued_at=issued_at,
            signature=signature,
        ):
            return AuthOutcome(False, "the HELLO signature did not match")

        if self._replayed(nonce, now):
            return AuthOutcome(False, "this HELLO nonce has already been used")

        return AuthOutcome(True, "signature verified")

    def _verify_with_key(
        self,
        *,
        device_id: str,
        name: str,
        os: str,
        nonce: str,
        issued_at: datetime,
        signature: str,
    ) -> AuthOutcome | None:
        """Check the signature against this device's own registered key.

        Returns None when there is nothing to check against — no pairing registry, or a
        device that has never paired — which sends the caller to the bootstrap token path.

        The important asymmetry: a device the registry *knows* is judged only by its key,
        even if that key check fails. Falling back to the shared token for a paired device
        would mean pairing improved nothing, because anyone holding the enrolment token
        could still impersonate every machine. And a device the registry knows and has
        **revoked** fails here rather than falling through — revocation that a shared token
        can route around is not revocation.
        """
        if self._pairing is None:
            return None
        try:
            identifier = UUID(device_id)
        except (ValueError, AttributeError):
            return None

        if not self._pairing.known(identifier):
            return None  # never paired: the bootstrap token is the enrolment path

        credential = self._pairing.credential(identifier)
        if credential is None:
            return AuthOutcome(False, "this device's credential has been revoked")

        payload = hello_payload(
            device_id=device_id, name=name, os=os, nonce=nonce, issued_at=issued_at
        )
        if not credential.public_key.verify(payload, signature):
            return AuthOutcome(False, "the HELLO signature did not match this device's key")
        return AuthOutcome(True, "verified against the device's registered key")

    def _verify_with_token(
        self,
        *,
        device_id: str,
        name: str,
        os: str,
        nonce: str,
        issued_at: datetime,
        signature: str,
    ) -> bool:
        """The bootstrap path, for a device that has not paired yet (ADR 0013).

        Still here because enrolment has to start somewhere, and narrower than it was: it
        now authenticates only devices with no key on file. Once a device pairs, this path
        is closed for it permanently.
        """
        expected = sign(
            self._token or "",
            signing_payload(
                device_id=device_id, name=name, os=os, nonce=nonce, issued_at=issued_at
            ),
        )
        # compare_digest, not ==: a byte-by-byte comparison leaks where the mismatch is.
        return hmac.compare_digest(expected, signature)

    def _replayed(self, nonce: str, now: datetime) -> bool:
        """Remember nonces for as long as a captured frame could still be within skew."""
        cutoff = now - MAX_CLOCK_SKEW
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)

        if nonce in self._seen:
            return True

        self._seen[nonce] = now
        while len(self._seen) > MAX_REMEMBERED_NONCES:
            self._seen.popitem(last=False)
        return False
