"""Device authentication for TNP/1 (§9.1, PART 26).

A node is the one component that runs commands on the owner's actual machine and reports
back whether they worked. Both halves matter. An impostor node could act, but worse, it
could *lie*: report ``verified: true`` for something it never did. Verification is the
property the whole system rests on, and it is only worth as much as the identity of the
thing doing the verifying.

So the HELLO frame is signed, and the signature is checked — not merely required to be
present. The scheme here is deliberately small:

* one shared enrolment token, from the environment, never from a tracked file;
* HMAC-SHA256 over the fields that identify the node, so changing any of them invalidates
  the signature;
* ``hmac.compare_digest``, so a wrong token cannot be found one byte at a time;
* the frame's own timestamp plus a nonce, so a captured HELLO cannot be replayed.

This is bootstrap authentication and is documented as such in ADR 0013. The shared token
is its weak point: it authenticates *a* node, not *this* node. The upgrade path is already
modelled — ``device_credentials`` holds a per-device public key — and moving to Ed25519
changes only :meth:`DeviceAuthenticator.verify`, not the protocol or its callers.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

from thursday_shared.models import utcnow

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

    def __init__(self, token: str | None, *, required: bool = True) -> None:
        self._token = token
        self.required = required
        self._seen: OrderedDict[str, datetime] = OrderedDict()

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

        expected = sign(
            self._token or "",
            signing_payload(
                device_id=device_id, name=name, os=os, nonce=nonce, issued_at=issued_at
            ),
        )
        # compare_digest, not ==: a byte-by-byte comparison leaks where the mismatch is.
        if not hmac.compare_digest(expected, signature):
            return AuthOutcome(False, "the HELLO signature did not match")

        if self._replayed(nonce, now):
            return AuthOutcome(False, "this HELLO nonce has already been used")

        return AuthOutcome(True, "signature verified")

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
