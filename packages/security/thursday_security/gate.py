"""The identity gate: who, before what (§1, §20, §22, §23, §30, §31, §66).

§1 draws the flow and the order in it is the whole design:

    Identity Verification → Authentication Level → Permission Engine → Risk → Approval

This module is the arrow between the second and third boxes. It answers one question —
*is this person established well enough for a request of this risk to be worth judging?* — and
then gets out of the way.

**It is not a second Permission Engine, and the difference is worth being precise about.**
The engine decides whether an action may happen. The gate decides whether we know who is
asking. They can disagree in both directions and both disagreements are meaningful: the owner
at AUTH_LEVEL_4 still cannot delete a system directory (the engine refuses), and a perfectly
ordinary "read a file" still stops if nobody knows who is at the keyboard (the gate refuses).
Merging them produces a single number that means neither thing.

The structural consequence, and the thing to check if this is ever refactored: **the Permission
Engine has no parameter for identity.** No `auth_level`, no `user_id`, no session. An
under-authenticated request never reaches it, so it never has to be careful about one.

**Refusals say nothing (§30, §40).** "ไม่สามารถยืนยันสิทธิ์ของผู้ใช้งานได้" is the whole
answer. Not who the owner is, not whether that file exists, not how close the voice was — a
confidence number returned to a stranger is a gradient for them to climb, and a "no such file"
that differs from "not allowed" is a file listing.

**Two things are deliberately open to everyone (§66, §67).** Stopping is one: an emergency
stop reduces risk, so requiring authentication to stop something is a way to make an attack
worse. Asking who is there is the other, because "ไม่สามารถยืนยันสิทธิ์" has to be sayable to
somebody who has not been identified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thursday_core.logging import get_logger
from thursday_shared.enums import RiskLevel, risk_at_least

from thursday_security.identity import (
    DEFAULT_MODE,
    AuthenticationSession,
    AuthLevel,
    Factor,
    SecurityMode,
    UserKind,
    required_level,
)

log = get_logger(__name__)

#: The single sentence an unidentified caller ever gets (§30, §62, §77). One string, used
#: everywhere, so no caller can accidentally be more helpful than the others — the leak in
#: this area is always a well-meant extra clause.
CANNOT_VERIFY = "ไม่สามารถยืนยันสิทธิ์ของผู้ใช้งานได้"

#: What anyone may do without being identified at all (§66, §67). Deliberately tiny, and
#: everything on it *reduces* what Thursday is doing rather than extending it.
#:
#: `system.stop` is here because an emergency stop that required authentication would mean an
#: attacker's best move is to make identification fail — turning the safety control into
#: another thing to defeat. Stopping is always allowed; starting is not, which is §66's
#: distinction exactly.
ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        "system.stop",
        "system.emergency.stop",
        "identity.who",
        "identity.authenticate",
        "identity.recover",
    }
)

#: Actions that no amount of identity makes automatic, listed here only so the gate does not
#: pretend otherwise: they are the Permission Engine's to refuse, and the gate letting them
#: through means "we know who is asking", never "they may".
_NOT_THE_GATES_BUSINESS = "the permission engine still decides whether this may happen"


@dataclass(frozen=True)
class GateVerdict:
    """Whether identity is sufficient. Never whether the action is allowed.

    `decision` is deliberately not a `PolicyDecision` — sharing that vocabulary is how the two
    systems start being treated as one. This layer has three answers and none of them is
    "approved".
    """

    #: True when the request may proceed *to the Permission Engine*.
    sufficient: bool
    have: AuthLevel
    need: AuthLevel
    #: What would close the gap, for the owner (§18's `required_next_factor`).
    next_factor: Factor | None = None
    #: What the owner is told. For a stranger this is always `CANNOT_VERIFY`.
    message: str = ""
    #: For the audit trail and for a developer; never rendered to an unidentified caller.
    reason: str = ""

    def __bool__(self) -> bool:
        return self.sufficient


class IdentityGate:
    """Stands between a request and the Permission Engine.

    Holds no policy about actions. Given a session and a risk, it says whether the identity
    behind the request is good enough to bother judging it.
    """

    def __init__(
        self,
        *,
        mode: SecurityMode = DEFAULT_MODE,
        owner_only: bool = False,
        audit: Any = None,
    ) -> None:
        self.mode = mode
        #: §22. When set, an unidentified person cannot use Thursday at all.
        self.owner_only = owner_only
        self._audit = audit

    # ------------------------------------------------------------------ the decision

    def check(
        self,
        *,
        action: str,
        risk: RiskLevel,
        session: AuthenticationSession | None,
        now: Any = None,
    ) -> GateVerdict:
        """Is whoever is asking established well enough for a request of this risk?"""
        if action in ALWAYS_ALLOWED:
            # §66. Reducing risk never requires proving who you are.
            return GateVerdict(
                sufficient=True,
                have=AuthLevel.NONE,
                need=AuthLevel.NONE,
                reason="always available, identified or not",
            )

        need = required_level(risk, mode=self.mode, owner_only=self.owner_only)
        have = session.effective_level(now=now) if session is not None else AuthLevel.NONE

        # §32. A guest is identified and still not entitled to private things. Checked here
        # rather than through the level, because a guest at STRONG is still a guest — the bar
        # they fail is *who they are*, not how well they proved it.
        # `risk_at_least`, never `>`. RiskLevel is a StrEnum, so `risk > RiskLevel.LOW`
        # compares *strings* — and "HIGH" sorts below "LOW" alphabetically, so the naive
        # version silently let a guest take exactly the actions this clause exists to stop.
        # The enum module warns about this in its own comment; I wrote the bug anyway.
        if (
            session is not None
            and session.kind is UserKind.GUEST
            and risk_at_least(risk, RiskLevel.MEDIUM)
        ):
            return self._refuse(action, have, need, reason="guest may not take this kind of action")

        if have >= need:
            return GateVerdict(
                sufficient=True, have=have, need=need, reason=_NOT_THE_GATES_BUSINESS
            )

        return self._refuse(action, have, need, session=session, now=now)

    def _refuse(
        self,
        action: str,
        have: AuthLevel,
        need: AuthLevel,
        *,
        session: AuthenticationSession | None = None,
        reason: str = "",
        now: Any = None,
    ) -> GateVerdict:
        next_factor = _missing_factor(session, need)
        if self._audit is not None:
            # The action and the levels. Never the confidence, never the sample, never which
            # template was compared — §75's rule, and the audit trail is a place templates
            # would otherwise end up.
            self._audit(action=action, have=int(have), need=int(need))
        log.info("identity_insufficient", action=action, have=int(have), need=int(need))
        return GateVerdict(
            sufficient=False,
            have=have,
            need=need,
            next_factor=next_factor,
            message=CANNOT_VERIFY,
            reason=reason or f"needs level {int(need)}, has {int(have)}",
        )

    # ------------------------------------------------------------------ §29 privacy

    def privacy_mode(self, *, unknown_people_present: int) -> bool:
        """§29. Whether to stop reading private things aloud.

        Separate from authentication and not a level: the owner is perfectly authenticated
        and there is still somebody else in the room, and reading their email out is wrong
        for a reason that has nothing to do with who is typing.
        """
        return unknown_people_present > 0


def _missing_factor(session: AuthenticationSession | None, need: AuthLevel) -> Factor | None:
    """What would most plausibly close the gap (§18's `required_next_factor`).

    A suggestion, and pointedly not a promise: offering a factor is not agreeing to accept it,
    and the fusion engine still decides what the result is worth. §17 is the case that matters
    — voice alone never reaches a high-risk action, so a session that already has voice is
    told to add a face or a device rather than to speak again.
    """
    if session is None:
        return Factor.FACE
    have = session.factors
    if Factor.FACE not in have:
        return Factor.FACE
    if Factor.VOICE not in have and need >= AuthLevel.TWO_BIOMETRIC:
        return Factor.VOICE
    if not (have & {Factor.TRUSTED_DEVICE, Factor.OS_BIOMETRIC}):
        return Factor.OS_BIOMETRIC
    return Factor.PIN
