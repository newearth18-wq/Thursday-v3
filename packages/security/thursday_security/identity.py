"""Who is asking (§1, §3, §18, §19, §25, §26, §59, §61).

The spec opens by separating three things that systems habitually merge:

    IDENTITY    who is giving the instruction
    PERMISSION  what that person may do
    APPROVAL    whether this particular action happens now

*"สามสิ่งนี้ห้ามรวมเป็นระบบเดียวกัน."* This module is only the first. It answers "who, and how
sure are we" and stops there — it holds no policy, grants nothing, and decides no action.

**The structural expression of that rule is what the Permission Engine does not gain.** No
`auth_level` parameter, no identity argument, no face anywhere in `ActionRequest`. Identity is
checked *before* the engine (see `gate.py`), and an under-authenticated request never reaches
it. The merged design is seductive because it looks tidier — one call, one verdict — and it
produces a system where relaxing an identity threshold silently widens a permission, and where
nobody can answer "what may a guest do" without also reasoning about cameras.

**Confidence is never a permission.** A face match at 0.97 is evidence about a face. It is not
an entitlement, and the number never leaves this layer: §40 forbids telling anyone "เสียง
ใกล้เคียง 83%" because it hands an attacker a gradient to climb, and §62 says the owner hears
"พร้อมครับ" rather than a score. What crosses the boundary is a *level*, which is a decision,
not a measurement.

**An agent gets four fields (§59, §61).** `identity_id`, `auth_level`, `session_fresh`, and
what they may do. Never a template, never a sample, never a confidence. `AuthContext` is that
whole surface, and it is a separate type precisely so nothing else can be passed by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from thursday_shared.enums import RiskLevel


class UserKind(StrEnum):
    """§3. Who somebody is to this installation, not what they may do."""

    OWNER = "OWNER"
    AUTHORIZED = "AUTHORIZED"
    GUEST = "GUEST"
    UNKNOWN = "UNKNOWN"


class AuthLevel(IntEnum):
    """§19. How thoroughly the person at the other end has been established.

    An `IntEnum` because every use is a comparison against a floor, and a string comparison
    here would be the `RiskLevel` mistake with worse consequences.

    The levels are not "how confident is the model". They are "how many independent things
    would have to be defeated at once", which is the only question that survives contact with
    somebody actively trying — a single very confident factor is one photograph away from
    wrong, and two mediocre ones are not.
    """

    #: Nobody has been established. §31's locked mode.
    NONE = 0
    #: One biometric matched. Enough to be greeted; not enough to open anything private.
    SINGLE = 1
    #: A biometric plus a device that was trusted before today.
    DEVICE_BACKED = 2
    #: Two independent biometrics — face and voice.
    TWO_BIOMETRIC = 3
    #: Two biometrics plus a trusted device or an OS-held credential (§37).
    STRONG = 4


class Factor(StrEnum):
    """§4. What was actually checked. Kept as a set on the session so an audit entry can say
    *which* things were established without ever carrying what they matched against."""

    FACE = "FACE"
    VOICE = "VOICE"
    TRUSTED_DEVICE = "TRUSTED_DEVICE"
    OS_BIOMETRIC = "OS_BIOMETRIC"
    PIN = "PIN"
    RECOVERY_KEY = "RECOVERY_KEY"


#: Which factors are *independent* of each other for the purpose of counting to a level. A
#: face and a photo of a face are not two things; a face and a PIN are. Declared rather than
#: derived so that adding a factor forces somebody to decide what it is independent of.
BIOMETRIC_FACTORS: frozenset[Factor] = frozenset({Factor.FACE, Factor.VOICE})
POSSESSION_FACTORS: frozenset[Factor] = frozenset({Factor.TRUSTED_DEVICE, Factor.OS_BIOMETRIC})
KNOWLEDGE_FACTORS: frozenset[Factor] = frozenset({Factor.PIN, Factor.RECOVERY_KEY})


#: §20's ladder: the identity floor each risk band needs before the Permission Engine is even
#: consulted. Declared as data because it is a security parameter, and a security parameter
#: buried in an `if` is one nobody reviews.
#:
#: Note what this is *not*: it is not a permission table. It says how well somebody must be
#: known before their request is worth judging — the judging is still the engine's job, and it
#: can still refuse an action this table would have admitted.
REQUIRED_FOR_RISK: dict[RiskLevel, AuthLevel] = {
    # Mapped explicitly, including NONE. The first version left NONE out and let the
    # fail-closed default apply, which made a *risk-free* action demand the *strongest*
    # identity — backwards, and the kind of inversion that reads as "security" until
    # somebody notices trivia is the hardest thing to do.
    RiskLevel.NONE: AuthLevel.SINGLE,
    RiskLevel.LOW: AuthLevel.SINGLE,
    RiskLevel.MEDIUM: AuthLevel.DEVICE_BACKED,
    RiskLevel.HIGH: AuthLevel.DEVICE_BACKED,
    RiskLevel.CRITICAL: AuthLevel.TWO_BIOMETRIC,
}


class SecurityMode(StrEnum):
    """§21's presets. The owner picks one of four; nobody tunes a threshold."""

    RELAXED = "RELAXED"
    BALANCED = "BALANCED"
    STRICT = "STRICT"
    MAXIMUM = "MAXIMUM"


#: What each preset raises the floor to, applied on top of `REQUIRED_FOR_RISK`. A mode can
#: only ever make the requirement *stricter* — a preset that lowered a floor would be a
#: convenience setting that weakens security, which §58 settles: security outranks it.
MODE_FLOOR: dict[SecurityMode, AuthLevel] = {
    SecurityMode.RELAXED: AuthLevel.SINGLE,
    SecurityMode.BALANCED: AuthLevel.SINGLE,
    SecurityMode.STRICT: AuthLevel.TWO_BIOMETRIC,
    SecurityMode.MAXIMUM: AuthLevel.STRONG,
}

#: §21's recommended default, and §6 says it is what a normal install gets.
DEFAULT_MODE = SecurityMode.BALANCED


@dataclass(frozen=True)
class AuthContext:
    """The **entire** identity surface an agent or tool may see (§59, §61).

    Frozen, minimal, and a distinct type so that handing an agent something richer requires
    writing a different class rather than forgetting a field. §61 gives the shape verbatim:
    `authenticated`, `user_id`, `auth_level`, `session_fresh`. Nothing about faces, voices,
    templates, confidences or which factors were used — an agent that knew the voice score
    could report it, and §40 exists to stop exactly that reaching an attacker.
    """

    authenticated: bool
    user_id: str | None
    auth_level: int
    session_fresh: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "user_id": self.user_id,
            "auth_level": self.auth_level,
            "session_fresh": self.session_fresh,
        }


#: What an unauthenticated caller gets. A constant rather than a constructed default, so
#: "no identity" is one object everywhere and cannot drift into "authenticated with no user".
ANONYMOUS = AuthContext(authenticated=False, user_id=None, auth_level=0, session_fresh=False)


#: Below this, a match does not name anybody — the matchers refuse to make the claim and the
#: fusion engine refuses to count it. One constant rather than one per layer, because two
#: thresholds for the same question drift, and the drift here is a matcher asserting "this is
#: the owner, five percent" that a later layer has to remember to discard.
USABLE_CONFIDENCE = 0.5


@dataclass(frozen=True)
class IdentityClaim:
    """One provider's opinion, before fusion. Never authoritative on its own.

    `confidence` and `liveness` stay inside the identity layer — they are inputs to
    `IdentityFusionEngine`, and the thing that leaves is a level.
    """

    factor: Factor
    user_id: str | None
    #: 0–1. How well the sample matched a stored template.
    confidence: float = 0.0
    #: 0–1. How much this looked like a living person rather than a recording (§12, §14).
    #: Separate from `confidence` on purpose: a photograph of the owner matches *perfectly*
    #: and is alive not at all, so a single blended score would rank the attack above a
    #: slightly-off real face.
    liveness: float = 0.0
    #: What the provider noticed that argues against believing it (§13, §17).
    concerns: tuple[str, ...] = ()
    #: Whether there was actually something to match — a face in frame, speech in the audio.
    #:
    #: This is the difference between *absence of evidence* and *evidence of absence*, and
    #: §64 is the reason it has to exist. "No face observed" (nobody in frame, camera off) and
    #: "a face was observed and it is not the owner's" arrive at fusion as the same
    #: `user_id=None` otherwise — so a stranger sitting at the machine playing a recording of
    #: the owner would have their mismatching face silently discarded and be admitted on the
    #: voice alone. An observed biometric that matched nobody must *contradict*, not abstain.
    observed: bool = False

    @property
    def usable(self) -> bool:
        return self.user_id is not None and self.confidence > 0.0


#: How long a session stays "fresh" without any re-verification. §24 wants the owner not
#: re-challenged for every command; §26 wants it to end. Fifteen minutes is short enough that
#: walking away and someone else sitting down does not inherit the session by default, and
#: long enough that a working hour is not a series of interruptions.
FRESH_FOR = timedelta(minutes=15)

#: The outer bound. Past this a session is over whatever the presence signal says, because a
#: presence signal that never fails is indistinguishable from one that is broken.
MAX_SESSION = timedelta(hours=8)

#: Idle time after which the session degrades a level (§26, §28).
IDLE_BEFORE_DEGRADE = timedelta(minutes=5)


@dataclass
class AuthenticationSession:
    """§25. One person, established on one device, for a bounded time.

    The lifetime rules are §26's list, and they exist because the alternative — a session that
    lasts until something explicitly revokes it — fails open at exactly the moment it matters:
    the owner walks away and the session is still sitting there for whoever sits down next.
    """

    user_id: str
    kind: UserKind
    device_id: UUID | None = None
    auth_level: AuthLevel = AuthLevel.NONE
    factors: set[Factor] = field(default_factory=set)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Last time the owner *did* something, as distinct from last time they were checked.
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_id: UUID = field(default_factory=uuid4)
    #: §28. Cleared only on *confirmed* absence — the owner gone long enough to lock, or
    #: somebody else at the machine. Not set for a momentary step out of frame; see
    #: `presence_cap` for that, and `presence.py` for why the two are different.
    present: bool = True
    #: A ceiling applied while the owner is not currently observed but has not been gone long
    #: enough to lock (§28's "Authentication Level ลดลง").
    #:
    #: This exists because the two obvious designs are both wrong. Dropping to NONE the moment
    #: a face leaves the frame re-challenges somebody who reached for a coffee cup, and §24 is
    #: explicit that a system which asks constantly is one people switch off. Leaving the level
    #: untouched lets whoever sits down next inherit it. A ceiling degrades honestly: ordinary
    #: work continues, anything private stops.
    presence_cap: AuthLevel | None = None
    #: Why the session ended, if it has. Kept for the audit trail (§75).
    ended_reason: str = ""

    # ------------------------------------------------------------------ lifetime

    def expired(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return bool(self.ended_reason) or (now - self.started_at) >= MAX_SESSION

    def fresh(self, *, now: datetime | None = None) -> bool:
        """Whether the identity was established recently enough to act on without recheck."""
        now = now or datetime.now(UTC)
        if self.expired(now=now) or not self.present:
            return False
        return (now - self.last_verified_at) < FRESH_FOR

    def effective_level(self, *, now: datetime | None = None) -> AuthLevel:
        """The level this session is worth *now*, which is not always what it was worth.

        Degradation rather than expiry is §28's design: the owner stepping out for coffee
        should not have to re-enrol, but the session must stop being good enough to send an
        email. So it decays toward NONE instead of vanishing, and each step down is a
        separate decision the owner can see.
        """
        now = now or datetime.now(UTC)
        if self.expired(now=now):
            return AuthLevel.NONE
        level = self.auth_level
        if not self.present:
            # Confirmed absent. Somebody who is not there is not authenticated, whatever
            # they were a moment ago.
            return AuthLevel.NONE
        if self.presence_cap is not None:
            level = AuthLevel(min(level, self.presence_cap))
        if (now - self.last_verified_at) >= FRESH_FOR:
            level = AuthLevel(max(AuthLevel.NONE, level - 1))
        if (now - self.last_activity_at) >= IDLE_BEFORE_DEGRADE:
            level = AuthLevel(max(AuthLevel.NONE, level - 1))
        return level

    # ------------------------------------------------------------------ transitions

    def touch(self, *, now: datetime | None = None) -> None:
        """The owner did something. Activity, not verification — they are different facts."""
        self.last_activity_at = now or datetime.now(UTC)

    def verified(
        self, *, level: AuthLevel, factors: set[Factor], now: datetime | None = None
    ) -> None:
        now = now or datetime.now(UTC)
        self.auth_level = level
        self.factors |= factors
        self.last_verified_at = now
        self.last_activity_at = now
        self.present = True
        # A fresh check is the thing that lifts a presence ceiling: the owner was observed.
        self.presence_cap = None

    def end(self, reason: str) -> None:
        self.ended_reason = reason

    # ------------------------------------------------------------------ what escapes

    def context(self, *, now: datetime | None = None) -> AuthContext:
        """The four fields §61 allows out of this layer, and nothing else."""
        now = now or datetime.now(UTC)
        level = self.effective_level(now=now)
        return AuthContext(
            authenticated=level > AuthLevel.NONE,
            user_id=self.user_id if level > AuthLevel.NONE else None,
            auth_level=int(level),
            session_fresh=self.fresh(now=now),
        )


def required_level(
    risk: RiskLevel, *, mode: SecurityMode = DEFAULT_MODE, owner_only: bool = False
) -> AuthLevel:
    """How well somebody must be known before an action of this risk is worth judging (§20).

    The mode raises the floor and never lowers it — `max`, not a lookup — because a
    convenience preset that weakened a security requirement is the thing §58 settles.

    `owner_only` is §22: with it on, even a low-risk action needs a real identity, because the
    setting means "nobody else uses this at all" rather than "nobody else does anything big".
    """
    floor = REQUIRED_FOR_RISK.get(risk, AuthLevel.STRONG)
    floor = AuthLevel(max(floor, MODE_FLOOR[mode]))
    if owner_only:
        floor = AuthLevel(max(floor, AuthLevel.SINGLE))
    return floor
