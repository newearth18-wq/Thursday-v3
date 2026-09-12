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

from thursday_security.enrolment import Rotation
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
        rotation: Rotation | None = None,
    ) -> None:
        # `rotation` supersedes `token` when both are given. One token is the ordinary
        # case and stays the simplest call; a rotation is the same thing with an overlap
        # window (§117, ADR 0066).
        self._rotation = rotation or (Rotation(current=token) if token else None)
        self.required = required
        self._seen: OrderedDict[str, datetime] = OrderedDict()
        #: The registry of per-device public keys (Sprint 36). When a device has paired,
        #: its own key is the only thing that authenticates it and the shared token stops
        #: working for it — which is the entire point of pairing.
        self._pairing = pairing

    @property
    def configured(self) -> bool:
        return self._rotation is not None

    @property
    def rotation(self) -> Rotation | None:
        """What the owner may see about the enrolment tokens. Never the secrets."""
        return self._rotation

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

        if not signature:
            return AuthOutcome(False, "the HELLO frame carried no signature")

        skew = abs(now - issued_at)
        if skew > MAX_CLOCK_SKEW:
            return AuthOutcome(
                False, f"HELLO timestamp is {skew.total_seconds():.0f}s from the core's clock"
            )

        token_used = ""
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
        elif not self.configured:
            # Nothing to check this signature against: the device has no registered key and
            # the core has no enrolment token. Fail closed — guessing that a deployment
            # which configured neither meant "allow everything" is how an unauthenticated
            # device ends up trusted in production.
            #
            # This check used to run *first*, before the key path, which quietly made the
            # recommended end state impossible: once every device has paired, the enrolment
            # token has no job left, and a core that dropped it refused every properly
            # paired machine. Pairing is what removes the need for the token (§80), so the
            # token's absence cannot be what refuses a paired device.
            return AuthOutcome(False, "no device token is configured on the core")
        else:
            matched = self._matching_token(
                device_id=device_id,
                name=name,
                os=os,
                nonce=nonce,
                issued_at=issued_at,
                signature=signature,
                now=now,
            )
            if matched is None:
                return AuthOutcome(False, self._token_refusal(now))
            token_used = matched

        if self._replayed(nonce, now):
            return AuthOutcome(False, "this HELLO nonce has already been used")

        if token_used and self._rotation is not None:
            # Which secret got this node in, by fingerprint. "Is anything still using
            # the old token?" is then a question the logs answer, rather than one the
            # owner settles by rotating and waiting to see what breaks.
            return AuthOutcome(True, f"signature verified with {self._rotation.label(token_used)}")
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

    def _matching_token(
        self,
        *,
        device_id: str,
        name: str,
        os: str,
        nonce: str,
        issued_at: datetime,
        signature: str,
        now: datetime,
    ) -> str | None:
        """The bootstrap path, for a device that has not paired yet (ADR 0013).

        Still here because enrolment has to start somewhere, and narrower than it was:
        it authenticates only devices with no key on file, and once a device pairs this
        path is closed for it permanently.

        Returns *which* token matched, so the caller can say so. Every live token is
        tried even after one matches: stopping early would make the time taken depend
        on which secret was used, and there are at most two.
        """
        if self._rotation is None:  # pragma: no cover - `configured` is checked first
            return None
        payload = signing_payload(
            device_id=device_id, name=name, os=os, nonce=nonce, issued_at=issued_at
        )
        found: str | None = None
        for token in self._rotation.accepts(now):
            # compare_digest, not ==: a byte-by-byte comparison leaks where the
            # mismatch is.
            if hmac.compare_digest(sign(token, payload), signature) and found is None:
                found = token
        return found

    def _token_refusal(self, now: datetime) -> str:
        """Why the signature did not match — including the case worth naming.

        A node that was mid-enrolment when the overlap ended signs with a token that
        used to work. "The signature did not match" sends its owner looking for a typo;
        the date the old token stopped working sends them to the right answer.
        """
        if self._rotation is not None and self._rotation.expired(now):
            retired = self._rotation.retires_at
            return (
                "the HELLO signature did not match — the previous enrolment token "
                f"stopped working on {retired.isoformat() if retired else 'its retirement date'}"
            )
        return "the HELLO signature did not match"

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
