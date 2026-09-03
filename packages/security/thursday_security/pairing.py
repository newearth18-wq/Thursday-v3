"""Device pairing (§80–83, Sprint 36).

    node generates a keypair → asks to pair → shows a code
                                    ↓
                        the owner reads the code and confirms it
                                    ↓
                    Core registers the public key · device is paired

Two separate proofs, and the flow is only worth anything because it has both.

**Proof of possession**, at `start`. The node signs its own request with the private key
belonging to the public key it is offering. Without this, anyone could register any key
under any name and the Core would faithfully trust it for ever.

**Proof of presence**, at `complete`. The code is shown on the device and typed by a person
into a client they already trust. Without this, proof of possession alone means any process
that can reach the API can enrol itself — which is not pairing, it is self-service.

The code is **not a credential** (§81). It authorises one enrolment, briefly, and the thing
that gets stored is the public key. That distinction is what stops it becoming the shared
secret this whole sprint exists to remove: a leaked code costs one pairing inside its
lifetime, and a leaked long-term token costs everything for ever.

What the Core stores is a public key. There is no code path here that accepts a private one.
"""

from __future__ import annotations

import secrets
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import TrustLevel
from thursday_shared.ids import new_id

from thursday_security.credentials import (
    CredentialStore,
    MemoryCredentialStore,
    StoredCredential,
)
from thursday_security.keys import PublicKey, pairing_payload, rotation_payload

log = get_logger(__name__)

#: How far a rotation request's own timestamp may sit from the core's clock, and how long
#: its nonce is remembered afterwards (twice this). Tighter than HELLO's five minutes: a
#: rotation is a deliberate act by a running node with a working clock, not a laptop waking
#: from sleep with a drifted one, and a captured rotation is worth more than a captured
#: HELLO — it names the key the core will trust next.
_ROTATION_SKEW = timedelta(minutes=2)

#: Memory ceiling for rotation nonces. Age is the real bound; this stops a node that
#: rotates in a loop from growing the set without limit.
_MAX_ROTATION_NONCES = 1024

#: How long a pairing code is good for. Minutes, not hours: the owner is standing at the
#: device reading it off a screen, so a short window costs nothing and a long one is a
#: credential lying around.
CODE_TTL = timedelta(minutes=5)

#: Digits in the code. Six is what people can read off a screen and type without error;
#: the security comes from the TTL, the single use and the rate limit, not from length.
CODE_DIGITS = 6

#: Failed completions allowed in a window before *all* completion is refused until the
#: window passes. Deliberately counted across every code rather than per code: a code is
#: what an attacker is guessing, so a per-code counter is one they never touch, and a
#: six-digit code with unlimited guesses is a five-minute brute force.
#:
#: The cost is that somebody spamming wrong codes can stop the owner pairing for ten
#: minutes. That is the right way round — pairing is rare and can wait; an attacker who can
#: guess without limit cannot be waited out.
MAX_ATTEMPTS = 5

#: New pairings a single caller may start in the window. Pairing is rare and human-paced;
#: anything doing it in bulk is not a person setting up a laptop (§81).
MAX_STARTS_PER_WINDOW = 5
RATE_WINDOW = timedelta(minutes=10)

#: How far a pairing request's own timestamp may sit from the Core's clock.
MAX_CLOCK_SKEW = timedelta(minutes=5)


class PairingError(Exception):
    """A pairing that cannot proceed. The message is safe to show the owner."""


@dataclass
class PendingPairing:
    """A device that has proved it holds a key and is waiting for a person to confirm."""

    id: UUID = field(default_factory=new_id)
    device_id: UUID = field(default_factory=new_id)
    code: str = ""
    public_key: str = ""
    name: str = ""
    os: str = ""
    hostname: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + CODE_TTL)
    attempts: int = 0
    completed_at: datetime | None = None

    def expired(self, *, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) >= self.expires_at

    @property
    def open(self) -> bool:
        return self.completed_at is None


@dataclass(frozen=True)
class DeviceCredential:
    """What the Core keeps about a paired device. Public material only."""

    device_id: UUID
    public_key: PublicKey
    name: str
    os: str
    hostname: str = ""
    algorithm: str = "ed25519"
    paired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    revoked_at: datetime | None = None
    #: When the device last replaced this key. None means it is still the pairing key.
    rotated_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    @property
    def fingerprint(self) -> str:
        return self.public_key.fingerprint

    @property
    def issued_at(self) -> datetime:
        """When the key in use now started being the key in use.

        Rotation resets the clock; pairing starts it. Age is a property of the *key*, not
        of the relationship with the device, and a device that rotated yesterday has a
        one-day-old key however long ago it first paired.
        """
        return self.rotated_at or self.paired_at

    def age(self, *, now: datetime | None = None) -> timedelta:
        return (now or datetime.now(UTC)) - self.issued_at

    def due_for_rotation(self, max_age: timedelta, *, now: datetime | None = None) -> bool:
        """Whether this key has been in use longer than the deployment intends.

        Reported, never enforced — see ADR 0042. A device key that expired on its own would
        turn "the owner was away for a while" into "every machine is locked out and must be
        re-paired by hand", which is the outage `credentials.py` exists to prevent, arriving
        on a timer instead of a restart.
        """
        return self.active and self.age(now=now) >= max_age


class PairingService:
    """Runs the pairing flow and owns the registry of device public keys."""

    def __init__(
        self,
        *,
        code_ttl: timedelta = CODE_TTL,
        max_attempts: int = MAX_ATTEMPTS,
        max_starts: int = MAX_STARTS_PER_WINDOW,
        rate_window: timedelta = RATE_WINDOW,
        store: CredentialStore | None = None,
    ) -> None:
        #: Pending pairings stay in memory deliberately. They live five minutes and mean
        #: "somebody is standing at a machine right now"; a core that restarts mid-pairing
        #: should make them start again, not resume a conversation with a person who has
        #: walked away.
        self._pending: dict[str, PendingPairing] = {}
        #: Rotation nonces seen inside the freshness window, so one cannot be replayed.
        self._rotation_nonces: dict[str, datetime] = {}
        self._store: CredentialStore = store or MemoryCredentialStore()
        self._credentials: dict[UUID, DeviceCredential] = {
            row.device_id: _restore(row) for row in self._store.load()
        }
        self._starts: dict[str, list[datetime]] = defaultdict(list)
        #: When completion was last refused. Guessing is the attack; this is the ledger.
        self._failures: list[datetime] = []
        self._ttl = code_ttl
        self._max_attempts = max_attempts
        self._max_starts = max_starts
        self._rate_window = rate_window

    # ------------------------------------------------------------------ step one

    def start(
        self,
        *,
        public_key: str,
        name: str,
        os: str,
        hostname: str = "",
        nonce: str,
        issued_at: datetime,
        signature: str,
        caller: str = "unknown",
        now: datetime | None = None,
    ) -> PendingPairing:
        """A node asks to pair, proving it holds the key it is offering.

        The signature is checked against the *submitted* public key. That sounds circular
        and is not: it proves the caller holds the private half of the key they are asking
        the Core to trust, which is exactly the claim being made. What it does not prove is
        that anybody wants this device paired — that is `complete`'s job.
        """
        now = now or datetime.now(UTC)
        self._enforce_rate_limit(caller, now)

        if abs((now - issued_at).total_seconds()) > MAX_CLOCK_SKEW.total_seconds():
            # A request timestamped far from now is either a badly-set clock or a captured
            # request being replayed, and neither should enrol a device.
            raise PairingError("this pairing request is too old or its clock is wrong")

        key = PublicKey(encoded=public_key)
        payload = pairing_payload(
            public_key=public_key,
            name=name,
            os=os,
            hostname=hostname,
            nonce=nonce,
            issued_at=issued_at,
        )
        if not key.verify(payload, signature):
            log.warning("pairing_signature_invalid", name=name, os=os)
            raise PairingError("the pairing request was not signed by the key it offers")

        pending = PendingPairing(
            code=self._new_code(),
            public_key=public_key,
            name=name,
            os=os,
            hostname=hostname,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._pending[pending.code] = pending
        log.info(
            "pairing_started",
            device=name,
            fingerprint=key.fingerprint,
            expires_in_s=int(self._ttl.total_seconds()),
        )
        return pending

    # ------------------------------------------------------------------ step two

    def complete(self, code: str, *, now: datetime | None = None) -> DeviceCredential:
        """A person confirms the code shown on the device.

        Every wrong guess is counted before the code is even looked up, because the guesses
        an attacker makes are for codes that do not exist and a counter hanging off the
        pending record would never see them.
        """
        now = now or datetime.now(UTC)
        self._enforce_guess_budget(now)
        pending = self._pending.get(code.strip())

        if pending is None:
            # Deliberately the same message as an expired code. Distinguishing "no such
            # code" from "code expired" tells an attacker which guesses were close.
            self._record_failure(now)
            raise PairingError("that pairing code is not valid")
        if not pending.open:
            # Single use (§81). A code that has already enrolled a device must not enrol a
            # second one, however soon after.
            self._record_failure(now)
            raise PairingError("that pairing code has already been used")
        if pending.expired(now=now):
            self._pending.pop(code, None)
            self._record_failure(now)
            raise PairingError("that pairing code is not valid")

        pending.attempts += 1
        credential = DeviceCredential(
            device_id=pending.device_id,
            public_key=PublicKey(encoded=pending.public_key),
            name=pending.name,
            os=pending.os,
            hostname=pending.hostname,
            paired_at=now,
        )
        self._credentials[credential.device_id] = credential
        self._persist()
        pending.completed_at = now
        self._pending.pop(code, None)

        log.info(
            "device_paired",
            device=credential.name,
            device_id=str(credential.device_id),
            fingerprint=credential.fingerprint,
        )
        return credential

    # ------------------------------------------------------------------ rotation (§117)

    def rotate(
        self,
        device_id: UUID,
        *,
        new_public_key: str,
        signature_by_old: str,
        signature_by_new: str,
        nonce: str,
        issued_at: datetime,
        now: datetime | None = None,
    ) -> DeviceCredential:
        """Replace a device's key with one it has just generated, without re-pairing.

        Pairing needs a person: they read a code off a screen and type it into a client they
        already trust. That is the right cost for enrolling a machine and the wrong cost for
        routine hygiene — if rotating means walking to every machine, nobody rotates, and
        §117 becomes a paragraph rather than a practice. So rotation is authorised by the
        key being replaced, not by a human.

        **Two signatures, and both are load-bearing.**

        `signature_by_old` proves the caller is the incumbent. It is the entire authority
        for this operation: whoever holds the current private key already *is* the device
        as far as the core can tell, so letting them name a successor grants nothing they
        did not already have.

        `signature_by_new` proves the caller holds the private half of the key it is asking
        the core to trust from now on. Nothing about security requires it — an attacker with
        the old key could sign both. It is here because without it, one malformed request
        replaces a working credential with a key nobody can sign for, and the machine is
        locked out until somebody walks over to it. Rotation that can brick a device by
        accident does not get used either.

        What this deliberately does **not** accept:

        · a device the registry does not know — rotation is not a second way to enrol;
        · a **revoked** device, however good its signatures. Revocation is sticky (§80), and
          a revoked key that could name its own replacement would be a way back in;
        · a stale or replayed request — same skew window and nonce memory as HELLO, because
          a captured rotation is worth strictly more to an attacker than a captured HELLO.
        """
        now = now or datetime.now(UTC)
        credential = self._credentials.get(device_id)

        if credential is None:
            raise PairingError("that device is not paired")
        if not credential.active:
            log.warning("rotation_refused_revoked", device_id=str(device_id))
            raise PairingError("that device is not paired")

        if abs((now - issued_at).total_seconds()) > _ROTATION_SKEW.total_seconds():
            raise PairingError("that rotation request is not fresh")
        if nonce in self._rotation_nonces:
            log.warning("rotation_replay_refused", device_id=str(device_id))
            raise PairingError("that rotation request has already been used")

        try:
            incoming = PublicKey(encoded=new_public_key)
        except ValueError as exc:
            raise PairingError("that is not a usable public key") from exc

        payload = rotation_payload(
            device_id=str(device_id),
            old_fingerprint=credential.fingerprint,
            new_public_key=new_public_key,
            nonce=nonce,
            issued_at=issued_at,
        )
        if not credential.public_key.verify(payload, signature_by_old):
            log.warning("rotation_refused_signature", device_id=str(device_id), by="current key")
            raise PairingError("that rotation request is not signed by the current key")
        if not incoming.verify(payload, signature_by_new):
            log.warning("rotation_refused_signature", device_id=str(device_id), by="new key")
            raise PairingError("that rotation request is not signed by the new key")

        self._remember_rotation_nonce(nonce, now)
        rotated = replace(credential, public_key=incoming, rotated_at=now)
        self._credentials[device_id] = rotated
        self._persist()

        # `paired_at` is intentionally left alone. It answers "when did the owner decide to
        # trust this machine", which rotation does not change and which nothing else records.
        log.info(
            "device_key_rotated",
            device=rotated.name,
            device_id=str(device_id),
            was=credential.fingerprint,
            now=rotated.fingerprint,
        )
        return rotated

    def due_for_rotation(
        self, max_age: timedelta, *, now: datetime | None = None
    ) -> list[DeviceCredential]:
        """Active credentials whose key has been in use longer than `max_age`.

        For the security dashboard (§133) and the morning brief. Being on this list changes
        nothing about whether the device works.
        """
        return [c for c in self._credentials.values() if c.due_for_rotation(max_age, now=now)]

    def _remember_rotation_nonce(self, nonce: str, now: datetime) -> None:
        """Bounded, and pruned by age rather than only by count.

        A count-only bound is a subtle replay hole: an attacker who can make the core rotate
        (or merely attempt) enough times pushes a captured nonce out of memory and can then
        replay it. Age is what actually matters, and the count is a memory ceiling on top.
        """
        cutoff = now - _ROTATION_SKEW * 2
        self._rotation_nonces = {n: t for n, t in self._rotation_nonces.items() if t > cutoff}
        self._rotation_nonces[nonce] = now
        while len(self._rotation_nonces) > _MAX_ROTATION_NONCES:
            self._rotation_nonces.pop(next(iter(self._rotation_nonces)))

    # ------------------------------------------------------------------ the registry

    def credential(self, device_id: UUID) -> DeviceCredential | None:
        """The active credential for a device, or None if unknown or revoked.

        Revoked reads as *unknown* on purpose. Every caller asking this question is asking
        "may I trust this device", and for that question the two answers are the same — so
        collapsing them removes a way for a caller to get it wrong.
        """
        credential = self._credentials.get(device_id)
        return credential if credential is not None and credential.active else None

    def known(self, device_id: UUID) -> bool:
        """Whether this device has ever been paired, revoked or not.

        Distinct from `credential` because revocation must be *sticky*: a revoked device
        that could re-pair itself has not been revoked, and `is_paired` is what stops the
        bootstrap token being a way back in.
        """
        return device_id in self._credentials

    def revoke(self, device_id: UUID, *, now: datetime | None = None) -> DeviceCredential | None:
        """Withdraw a device's identity. The record is kept, not deleted.

        Kept because "this device was revoked on Tuesday" is a fact somebody will need, and
        because a deleted credential would let the same device pair again as though nothing
        had happened.
        """
        credential = self._credentials.get(device_id)
        if credential is None:
            return None
        # `replace`, not a field-by-field rebuild. The rebuild listed every field it meant
        # to keep, so adding `rotated_at` to the dataclass would have silently reset it on
        # revocation — a copy that enumerates fields is a copy that goes stale the next time
        # somebody adds one, and does it quietly.
        revoked = replace(credential, revoked_at=now or datetime.now(UTC))
        self._credentials[device_id] = revoked
        self._persist()
        # Any pairing this device has in flight dies with it, or revocation is a race.
        for code, pending in list(self._pending.items()):
            if pending.device_id == device_id:
                self._pending.pop(code, None)
        log.warning("device_revoked", device=credential.name, device_id=str(device_id))
        return revoked

    def credentials(self, *, include_revoked: bool = False) -> list[DeviceCredential]:
        rows = list(self._credentials.values())
        return sorted(
            (c for c in rows if c.active or include_revoked),
            key=lambda c: c.paired_at,
            reverse=True,
        )

    def pending(self, *, now: datetime | None = None) -> list[PendingPairing]:
        now = now or datetime.now(UTC)
        return [p for p in self._pending.values() if p.open and not p.expired(now=now)]

    def prune(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        before = len(self._pending)
        self._pending = {c: p for c, p in self._pending.items() if not p.expired(now=now)}
        return before - len(self._pending)

    # ------------------------------------------------------------------ internals

    def _new_code(self) -> str:
        """A code from the CSPRNG, retried on the astronomically unlikely collision."""
        for _ in range(10):
            code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
            if code not in self._pending:
                return code
        raise PairingError("could not allocate a pairing code; try again")

    def _persist(self) -> None:
        """Write the whole registry after every change.

        Whole-file rather than incremental because the set is a handful of machines and the
        failure mode of a partial write here is a device that cannot connect.
        """
        self._store.save(
            [
                StoredCredential(
                    device_id=c.device_id,
                    public_key=c.public_key.encoded,
                    name=c.name,
                    os=c.os,
                    hostname=c.hostname,
                    algorithm=c.algorithm,
                    paired_at=c.paired_at,
                    revoked_at=c.revoked_at,
                    rotated_at=c.rotated_at,
                )
                for c in self._credentials.values()
            ]
        )

    def _record_failure(self, now: datetime) -> None:
        self._failures.append(now)

    def _enforce_guess_budget(self, now: datetime) -> None:
        """Refuse completion outright once too many have failed recently.

        Checked before the code is looked up, so a guess costs the attacker the same whether
        or not it was close, and an exhausted budget cannot be probed for near misses.
        """
        cutoff = now - self._rate_window
        self._failures = [t for t in self._failures if t > cutoff]
        if len(self._failures) >= self._max_attempts:
            log.warning("pairing_completion_locked", failures=len(self._failures))
            raise PairingError(
                "too many incorrect pairing codes; wait a few minutes and start again"
            )

    def _enforce_rate_limit(self, caller: str, now: datetime) -> None:
        """Pairing is rare and human-paced. Anything doing it in bulk is not a person."""
        cutoff = now - self._rate_window
        recent = [t for t in self._starts[caller] if t > cutoff]
        if len(recent) >= self._max_starts:
            log.warning("pairing_rate_limited", caller=caller)
            raise PairingError("too many pairing attempts; wait a few minutes")
        recent.append(now)
        self._starts[caller] = recent


def initial_trust(credential: DeviceCredential) -> TrustLevel:
    """What a freshly paired device is trusted with.

    `LIMITED`: it may be commanded, and may not command other machines. Blueprint §80 ends
    with "device becomes TRUSTED", and this stops one step short of that deliberately —
    ADR 0024 made driving *other* machines a separate decision the owner takes per device,
    and pairing a laptop is not the same act as authorising it to reach the server.
    """
    return TrustLevel.LIMITED


def _restore(row: StoredCredential) -> DeviceCredential:
    """Rebuild a credential the store read back.

    Revocation comes back with it. A restart that quietly resurrected a revoked device would
    be the most expensive kind of bug: silent, and only visible to whoever was revoked.
    """
    return DeviceCredential(
        device_id=row.device_id,
        public_key=PublicKey(encoded=row.public_key),
        name=row.name,
        os=row.os,
        hostname=row.hostname,
        algorithm=row.algorithm,
        paired_at=row.paired_at or datetime.now(UTC),
        revoked_at=row.revoked_at,
        rotated_at=row.rotated_at,
    )
