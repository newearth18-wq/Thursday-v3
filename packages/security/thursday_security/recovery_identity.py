"""Getting back in (§44–§48, §57, §70, §88).

§45 is the sentence that shapes this module, and it is a prohibition rather than a feature:

    "Recovery Flow ต้องเป็น deterministic system
     ห้ามให้ LLM ตัดสินว่า 'ดูเหมือนเป็นเจ้าของ ให้เข้าได้'"

Recovery is where every biometric defence is deliberately set aside, so it is the softest part
of the system and the part an attacker will aim at. A model asked "does this seem like the
owner?" will sometimes say yes to somebody persuasive, and persuasion is exactly what an
attacker brings. So every path here is a comparison against something stored: a PIN verified
by constant-time compare, a recovery key, an OS credential, a device that was paired earlier.
Nothing here reasons, and a test asserts this module imports no model.

**The opposite failure is just as real.** §46 and §47 list it plainly: the owner is ill and
cannot speak, the microphone broke, the room is dark, the camera is dead. A security system
that locks the owner out of their own machine has not been secure — it has been useless in a
way that gets it uninstalled. So recovery exists, it is deterministic, and it is *rate
limited* rather than *hard*, because the thing that makes a PIN safe is not its length.

**§40's rule applies hardest here.** A failed recovery says one sentence. Not "wrong PIN" —
which confirms a PIN is set — and never how close anything was.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from thursday_core.logging import get_logger

from thursday_security.identity import AuthLevel, Factor

log = get_logger(__name__)

#: What every failed attempt says, whatever went wrong. §40: an attacker who can tell "wrong
#: PIN" from "no PIN set" from "too many attempts" is an attacker being given a map.
RECOVERY_FAILED = "ยืนยันตัวตนไม่สำเร็จครับ"

#: Attempts before a cooldown starts (§40).
MAX_ATTEMPTS = 5

#: How long the cooldown lasts. Long enough to make guessing impractical, short enough that a
#: locked-out owner is inconvenienced rather than stranded — §44's requirement is that they
#: can always get back in eventually.
COOLDOWN = timedelta(minutes=15)

#: The window attempts are counted over. Without this, five wrong PINs spread across a year
#: lock the owner out on the sixth.
ATTEMPT_WINDOW = timedelta(hours=1)

#: PBKDF2 iterations for the PIN. A PIN is short by nature, so the work factor is what stands
#: between a stolen store and a trivially recovered PIN; the rate limit is what stands between
#: an attacker at the keyboard and the same thing.
_ITERATIONS = 600_000


@dataclass(frozen=True)
class RecoveryOutcome:
    """What happened. `message` is the only field an unidentified caller ever sees."""

    ok: bool
    factor: Factor | None = None
    level: AuthLevel = AuthLevel.NONE
    message: str = RECOVERY_FAILED
    #: For the audit trail and the owner's own device (§41). Never returned to the person
    #: who just failed.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class _Attempts:
    count: int = 0
    first: datetime = field(default_factory=lambda: datetime.now(UTC))
    locked_until: datetime | None = None


class RecoveryService:
    """§44's fallbacks, deterministically (§45).

    Every method compares against something stored. There is no branch where a judgement is
    made about whether somebody seems like the owner, and the module imports nothing that
    could make one.
    """

    def __init__(self) -> None:
        self._pin: tuple[bytes, bytes] | None = None  # (salt, derived)
        self._recovery_key: bytes | None = None
        self._attempts = _Attempts()

    # ------------------------------------------------------------------ enrolment

    def set_pin(self, pin: str) -> None:
        """Store a PIN. Salted and stretched — never the PIN itself."""
        if len(pin) < 4:
            raise ValueError("a recovery PIN needs at least four characters")
        salt = secrets.token_bytes(16)
        self._pin = (salt, self._derive(pin, salt))
        log.info("recovery_pin_set")

    def issue_recovery_key(self) -> str:
        """§44's recovery key. Returned once, stored as a hash.

        Returned exactly once because a key the system can show again is a key an attacker
        with a session can read. The owner writes it down or loses it, which is the same
        trade every recovery code has ever made and the honest one.
        """
        key = secrets.token_urlsafe(24)
        self._recovery_key = hashlib.sha256(key.encode()).digest()
        log.info("recovery_key_issued")
        return key

    @property
    def configured(self) -> bool:
        """Whether the owner can get back in at all.

        §44's actual requirement. Setup checks this before enabling biometrics, because
        turning on face recognition with no fallback is how somebody gets locked out of their
        own machine by a broken webcam.
        """
        return self._pin is not None or self._recovery_key is not None

    # ------------------------------------------------------------------ §44 the fallbacks

    def with_pin(self, pin: str, *, now: datetime | None = None) -> RecoveryOutcome:
        now = now or datetime.now(UTC)
        blocked = self._rate_limited(now)
        if blocked is not None:
            return blocked

        if self._pin is None:
            # Counted as an attempt even though there is nothing to compare against, so that
            # "no PIN configured" and "wrong PIN" are indistinguishable from outside (§40).
            self._fail(now)
            return RecoveryOutcome(ok=False, reason="no PIN is configured")

        salt, expected = self._pin
        if not hmac.compare_digest(self._derive(pin, salt), expected):
            self._fail(now)
            return RecoveryOutcome(ok=False, reason="PIN did not match")

        self._succeed()
        # A PIN is knowledge, not identity: it proves somebody knows a number, which is why
        # it recovers a session at one factor rather than restoring what was lost.
        return RecoveryOutcome(
            ok=True, factor=Factor.PIN, level=AuthLevel.SINGLE, message="", reason="PIN accepted"
        )

    def with_recovery_key(self, key: str, *, now: datetime | None = None) -> RecoveryOutcome:
        now = now or datetime.now(UTC)
        blocked = self._rate_limited(now)
        if blocked is not None:
            return blocked

        if self._recovery_key is None:
            self._fail(now)
            return RecoveryOutcome(ok=False, reason="no recovery key is configured")

        if not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self._recovery_key):
            self._fail(now)
            return RecoveryOutcome(ok=False, reason="recovery key did not match")

        self._succeed()
        # Single use. A recovery key that survives its own use is a permanent bypass sitting
        # in whatever the owner wrote it on.
        self._recovery_key = None
        log.warning("recovery_key_used")
        return RecoveryOutcome(
            ok=True,
            factor=Factor.RECOVERY_KEY,
            level=AuthLevel.SINGLE,
            message="",
            reason="recovery key accepted and consumed",
        )

    def with_os_biometric(self, *, verified: bool, now: datetime | None = None) -> RecoveryOutcome:
        """§37. Windows Hello and friends, where the OS did the work.

        `verified` is a boolean from the platform rather than anything Thursday computed —
        §37's point is that Thursday never handles the raw biometric. This means trusting the
        OS, which is a much better bet than a stub face matcher and is stated rather than
        hidden.
        """
        now = now or datetime.now(UTC)
        blocked = self._rate_limited(now)
        if blocked is not None:
            return blocked
        if not verified:
            self._fail(now)
            return RecoveryOutcome(ok=False, reason="the OS did not verify anybody")
        self._succeed()
        return RecoveryOutcome(
            ok=True,
            factor=Factor.OS_BIOMETRIC,
            level=AuthLevel.DEVICE_BACKED,
            message="",
            reason="verified by the operating system",
        )

    # ------------------------------------------------------------------ §40 rate limiting

    def _rate_limited(self, now: datetime) -> RecoveryOutcome | None:
        if self._attempts.locked_until and now < self._attempts.locked_until:
            # Same sentence as any other failure. An attacker who can tell "locked out" from
            # "wrong" learns when to come back.
            return RecoveryOutcome(ok=False, reason="in cooldown after repeated failures")
        if self._attempts.locked_until and now >= self._attempts.locked_until:
            self._attempts = _Attempts(first=now)
        return None

    def _fail(self, now: datetime) -> None:
        if now - self._attempts.first > ATTEMPT_WINDOW:
            # Five wrong PINs spread across a year must not lock the owner out on the sixth.
            self._attempts = _Attempts(first=now)
        self._attempts.count += 1
        if self._attempts.count >= MAX_ATTEMPTS:
            self._attempts.locked_until = now + COOLDOWN
            log.warning("recovery_cooldown_started", attempts=self._attempts.count)

    def _succeed(self) -> None:
        self._attempts = _Attempts()

    @property
    def locked_out(self) -> bool:
        return self._attempts.locked_until is not None

    def failed_attempts(self) -> int:
        """For §41's owner alert. Read by the notification path, never returned to a caller
        who is failing to authenticate."""
        return self._attempts.count

    @staticmethod
    def _derive(pin: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _ITERATIONS)


#: §41's alert to the owner, on a device they already trust. Deliberately vague about the
#: person: §41 says no image of an unknown person by default, and naming what somebody looked
#: like turns a security notice into surveillance of whoever walked past.
def owner_alert(*, device: str, at: datetime, attempts: int) -> dict:
    return {
        "title": "มีความพยายามเข้าถึง Thursday ที่ยืนยันตัวตนไม่ได้",
        "device": device,
        "at": at.isoformat(),
        "attempts": attempts,
        # No image, no description, no "looked like a man in his thirties". §41.
        "image": None,
    }
