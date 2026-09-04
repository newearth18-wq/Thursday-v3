"""Putting the evidence together (§18, §19, §20, §36, §37, §65, §86, §90).

§90 states what fusion is for, and it is a warning rather than a feature list:

    Thursday ต้อง "รู้จักเจ้าของ" แต่ไม่ควรเชื่อเพียงเพราะ
    เห็นหน้าคล้าย · ได้ยินเสียงคล้าย · อยู่บนเครื่องเดิม

Each factor on its own is a resemblance. A face that looks right, a voice that sounds right, a
machine that was the owner's yesterday — each is a thing an attacker can arrange, and each is
individually convincing. Fusion exists because the interesting question is not "how confident
is the best factor" but **"how many independent things would have to be defeated at once"**.

So the level is computed by *counting independent kinds of evidence*, never by summing
confidences. A 0.99 face and a 0.99 voice are not more independent than a 0.7 face and a 0.7
voice; they are the same two things, more confidently. Summing would let one excellent factor
buy a level that two mediocre independent ones could not, which is exactly backwards — one
excellent factor is one photograph away from wrong.

**The ceilings are absolute and imported, not decided here.**

    voice alone      never past `VOICE_ALONE_CEILING` (§17), imported from `voice.py`
    device alone     never reaches a critical action (§36) — possession is not identity
    no liveness      contributes nothing at all, whatever it matched (§12, §13)

A ceiling that this module could compute is a ceiling this module could raise. Importing them
means changing one requires editing the file where the spec sentence is quoted.

**Device trust is not identity (§36).** A trusted device says *where* a request came from, and
somebody holding an unlocked laptop has that. It raises a level built on a biometric and never
constitutes one, which is why it appears in the count only alongside something a person is.
"""

from __future__ import annotations

from dataclasses import dataclass

from thursday_core.logging import get_logger

from thursday_security.identity import (
    BIOMETRIC_FACTORS,
    KNOWLEDGE_FACTORS,
    POSSESSION_FACTORS,
    AuthLevel,
    Factor,
    IdentityClaim,
    UserKind,
)
from thursday_security.voice import VOICE_ALONE_CEILING

log = get_logger(__name__)

#: A claim below this confidence is not evidence of anybody. Deliberately not a tuning dial
#: for "how strict is Thursday" — that is the security mode's job, on the requirement side.
#: This is the floor below which a match is noise.
USABLE_CONFIDENCE = 0.5

#: §12/§13. A biometric claim with no liveness contributes nothing, whatever it matched: a
#: photograph and a recording both match perfectly and neither is a person.
LIVE_ENOUGH = 0.6

#: §36. Possession alone never reaches a critical action. Somebody holding the owner's
#: unlocked laptop has device trust and is not the owner.
DEVICE_ONLY_CEILING = AuthLevel.SINGLE


@dataclass(frozen=True)
class Fused:
    """§18's output. A level, and what would raise it."""

    user_id: str | None
    level: AuthLevel
    factors: frozenset[Factor] = frozenset()
    #: §18's `required_next_factor`. A suggestion, never a promise to accept it.
    next_factor: Factor | None = None
    #: Why the level is what it is. For the audit trail and for a developer — never for an
    #: unidentified caller, who gets `CANNOT_VERIFY` and nothing else (§30, §40).
    reasons: tuple[str, ...] = ()

    @property
    def identified(self) -> bool:
        return self.user_id is not None and self.level > AuthLevel.NONE


class IdentityFusionEngine:
    """§18. Turns claims plus context into one level.

    Holds no policy about actions and no thresholds about risk: it answers "who, and how
    well established", and the gate compares that against what the action needs.
    """

    def fuse(
        self,
        claims: list[IdentityClaim],
        *,
        device_trusted: bool = False,
        os_authenticated: bool = False,
        kind: UserKind = UserKind.OWNER,
    ) -> Fused:
        """Combine what each provider said into a level.

        `device_trusted` and `os_authenticated` arrive as booleans rather than as claims
        because they are not opinions about a person: the OS either authenticated somebody or
        it did not, and a device either presented a credential enrolled earlier or it did not.
        Modelling them as confidences would invite averaging them with a face.
        """
        reasons: list[str] = []
        usable: dict[Factor, IdentityClaim] = {}
        subjects: set[str] = set()

        for claim in claims:
            ok, why = self._usable(claim)
            if not ok:
                reasons.append(f"{claim.factor.value.lower()}: {why}")
                continue
            usable[claim.factor] = claim
            if claim.user_id:
                subjects.add(claim.user_id)

        factors: set[Factor] = set(usable)
        if device_trusted:
            factors.add(Factor.TRUSTED_DEVICE)
        if os_authenticated:
            factors.add(Factor.OS_BIOMETRIC)

        if len(subjects) > 1:
            # Two factors naming two different people is not two factors — it is a face and a
            # voice that disagree, which is §64's scenario: an unknown face at the keyboard
            # playing a recording of the owner. The honest answer is that nobody is
            # established, not that the majority wins.
            log.warning("identity_factors_disagree", subjects=len(subjects))
            return Fused(
                user_id=None,
                level=AuthLevel.NONE,
                factors=frozenset(factors),
                next_factor=Factor.OS_BIOMETRIC,
                reasons=("the factors identified different people",),
            )

        user_id = next(iter(subjects), None)
        if user_id is None:
            return Fused(
                user_id=None,
                level=AuthLevel.NONE,
                factors=frozenset(factors),
                next_factor=Factor.FACE,
                reasons=tuple(reasons) or ("nothing identified anybody",),
            )

        level = self._level_for(factors)
        level = self._apply_ceilings(level, factors, reasons)

        if kind is UserKind.GUEST:
            # A guest is identified and stays a guest. The gate refuses them serious actions
            # by *kind*; the level here is about how well they were established.
            reasons.append("identified as a guest")

        return Fused(
            user_id=user_id,
            level=level,
            factors=frozenset(factors),
            next_factor=self._next(factors, level),
            reasons=tuple(reasons),
        )

    # ------------------------------------------------------------------ what counts

    def _usable(self, claim: IdentityClaim) -> tuple[bool, str]:
        """Whether a claim is evidence of anybody at all."""
        if claim.user_id is None:
            return False, "identified nobody"
        if claim.confidence < USABLE_CONFIDENCE:
            return False, "matched too weakly to mean anything"
        if claim.factor in BIOMETRIC_FACTORS and claim.liveness < LIVE_ENOUGH:
            # §12/§13. The photograph case, at the fusion boundary as well as inside the
            # matcher — belt and braces, because this is the join where a future provider
            # that forgot to check liveness would otherwise be believed.
            return False, "nothing indicated a living person"
        return True, ""

    def _level_for(self, factors: set[Factor]) -> AuthLevel:
        """Count independent kinds. Never sum confidences.

        Three kinds: something you *are* (biometric), something you *have* (possession), and
        something you *know*. Two of the same kind is worth less than one of each, which is
        why the count is over kinds rather than over factors.
        """
        biometrics = factors & BIOMETRIC_FACTORS
        possession = factors & POSSESSION_FACTORS
        knowledge = factors & KNOWLEDGE_FACTORS

        if not biometrics and not possession and not knowledge:
            return AuthLevel.NONE

        if len(biometrics) >= 2 and possession:
            return AuthLevel.STRONG
        if len(biometrics) >= 2:
            return AuthLevel.TWO_BIOMETRIC
        if biometrics and (possession or knowledge):
            return AuthLevel.DEVICE_BACKED
        if biometrics:
            return AuthLevel.SINGLE
        # No biometric at all: possession or a PIN. Both are things somebody could be holding
        # or have watched being typed, so neither establishes a person on its own.
        return AuthLevel.SINGLE

    def _apply_ceilings(
        self, level: AuthLevel, factors: set[Factor], reasons: list[str]
    ) -> AuthLevel:
        """The absolute limits, imported rather than decided here."""
        biometrics = factors & BIOMETRIC_FACTORS

        if biometrics == {Factor.VOICE}:
            # §17. A voice matching the owner is true of a recording and true of a clone.
            if level > VOICE_ALONE_CEILING:
                reasons.append("voice alone is never more than one factor")
            level = AuthLevel(min(level, VOICE_ALONE_CEILING))

        if not biometrics:
            # §36. Possession is not identity: somebody holding an unlocked laptop has the
            # device and is not the owner.
            if level > DEVICE_ONLY_CEILING:
                reasons.append("a device alone does not establish a person")
            level = AuthLevel(min(level, DEVICE_ONLY_CEILING))

        return level

    def _next(self, factors: set[Factor], level: AuthLevel) -> Factor | None:
        """What would most plausibly raise this. §18's `required_next_factor`.

        Offers the factor that adds a *kind* rather than more of what is already there —
        asking a voice-only session to speak again would not move the level, and asking for
        something it cannot supply is how a system trains people to give up.
        """
        if level is AuthLevel.STRONG:
            return None
        biometrics = factors & BIOMETRIC_FACTORS
        if Factor.FACE not in biometrics:
            return Factor.FACE
        if Factor.VOICE not in biometrics:
            return Factor.VOICE
        if not (factors & POSSESSION_FACTORS):
            return Factor.OS_BIOMETRIC
        return None


@dataclass
class DeviceTrust:
    """§36. What makes a machine trusted, and what that is worth.

    Every input is about the *device*, and the type says so: nothing here is evidence about a
    person. It exists so that "the laptop is trusted" cannot quietly become "the owner is
    here" — the fusion engine takes it as one boolean and can never mistake it for a face.
    """

    paired: bool = False
    #: The device presented the credential enrolled during pairing (Sprint 36).
    credential_valid: bool = False
    #: The OS session is unlocked. Weak on its own — an unlocked laptop is a laptop somebody
    #: walked away from.
    os_unlocked: bool = False
    #: Seen on a network the owner has used before. Weakest of all; a network is not a person.
    known_network: bool = False
    recently_authenticated: bool = False

    @property
    def trusted(self) -> bool:
        """The bar for counting as a trusted device at all.

        Pairing plus a valid credential, and nothing less. The softer signals adjust
        confidence in that judgement; they never substitute for it, because "on the usual
        wifi" is true of everyone in the building.
        """
        return self.paired and self.credential_valid

    def reasons(self) -> list[str]:
        out = []
        if not self.paired:
            out.append("this device has not been paired")
        elif not self.credential_valid:
            out.append("this device did not present its credential")
        return out
