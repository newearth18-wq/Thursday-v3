"""Staying authenticated, and stopping (§24, §26–§31, §68, §80, §81, §87).

Everything before this sprint answers "who is at the keyboard *now*". This one answers the
question that actually decides whether the whole scheme is worth anything: **what happens
when they leave**.

§80 is the scenario, and it is the ordinary one rather than the exotic one. The owner
authenticates, works, and walks away. Somebody else sits down. No attack was mounted, nothing
was spoofed, and if the session is still open then every defence in the previous four sprints
was decoration.

So the design has three parts and they are deliberately not the same mechanism:

    presence     is the owner still here?          — an observation, and it can be wrong
    degradation  what is the session worth now?    — derived, continuous, reversible
    privacy      who else can see and hear this?   — independent of both

**Presence failing open is the whole risk.** A camera that stops reporting, a stale frame, a
process that hung — each looks exactly like "the owner is still sitting there" if the code
treats "no news" as "no change". So presence *expires*: a signal that has not been renewed is
absence, not continuity. §26 lists the things that end a session and every one of them is a
positive event; this module adds the negative one, which is the passage of time with nothing
observed.

**§24 pulls the other way, and it is a real requirement, not a nicety.** Thursday must not
re-challenge the owner for every command. A system that asks constantly is one people disable,
and a disabled system protects nobody — so the answer is a session that stays valid while
presence holds and degrades smoothly rather than one that expires abruptly and demands a
ceremony. That is why degradation is a level falling rather than a session ending.

**Privacy mode is separate from authentication (§29).** The owner is perfectly authenticated
and there is still somebody else in the room. Reading their email aloud is wrong for a reason
that has nothing to do with who is typing, so it is a different switch with different inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from thursday_core.logging import get_logger

from thursday_security.identity import AuthenticationSession, AuthLevel

log = get_logger(__name__)

#: How long a presence observation stays meaningful. After this, silence is read as absence.
#:
#: The direction matters more than the number: a presence signal that never goes stale is
#: indistinguishable from a camera that stopped reporting, and the failure mode of guessing
#: "still there" is somebody else inheriting the owner's session.
PRESENCE_TTL = timedelta(seconds=90)

#: How long after the owner is last seen before the session locks outright (§28).
#: Longer than the TTL, because stepping out of frame to reach a coffee cup should degrade
#: rather than lock — §24's requirement that Thursday not be exhausting.
AWAY_BEFORE_LOCK = timedelta(minutes=3)

#: What a session is worth while the owner is out of frame but not yet gone. One factor:
#: enough to keep music playing and answer a question, not enough for anything private.
#: §28's ladder in a constant — the level falls before the lock arrives, rather than the two
#: being the same event.
DEGRADED_TO = AuthLevel.SINGLE


class Presence(StrEnum):
    """What the room looks like. Three states, and the third is not the second."""

    #: An authorised person has been observed recently.
    PRESENT = "PRESENT"
    #: Nobody has been observed recently. The signal is stale, or the seat is empty.
    ABSENT = "ABSENT"
    #: Somebody is there and is not recognised. Distinct from ABSENT because it is *worse*:
    #: an empty chair is not a risk, and a stranger at the keyboard is.
    UNKNOWN_PERSON = "UNKNOWN_PERSON"


@dataclass(frozen=True)
class Observation:
    """One look at the room. Never an image — counts and identities only (§9, §27).

    §27 says continuous authentication should not stream video anywhere, and the shape of
    this type is how that is kept true: there is nowhere to put a frame, so nothing
    downstream can receive one.
    """

    at: datetime
    #: Authorised people recognised, by id.
    recognised: frozenset[str] = frozenset()
    #: How many faces were seen that were not recognised.
    unknown_people: int = 0

    @property
    def anybody(self) -> bool:
        return bool(self.recognised) or self.unknown_people > 0


@dataclass
class PresenceMonitor:
    """Watches the room and reports what a session is worth (§27, §28, §87).

    Holds observations, not media. The camera layer decides what counts as recognised; this
    decides what that means for the session.
    """

    _last: Observation | None = None
    _last_seen: dict[str, datetime] = field(default_factory=dict)

    def observe(self, observation: Observation) -> None:
        self._last = observation
        for user_id in observation.recognised:
            self._last_seen[user_id] = observation.at

    def state(self, *, user_id: str, now: datetime | None = None) -> Presence:
        """Where this person stands, as of now.

        Staleness is checked before content: an observation from four minutes ago saying the
        owner was there is not evidence that the owner is there. That ordering is the whole
        defence against a stuck camera.
        """
        now = now or datetime.now(UTC)
        last = self._last
        if last is None or (now - last.at) > PRESENCE_TTL:
            return Presence.ABSENT
        if user_id in last.recognised:
            return Presence.PRESENT
        if last.unknown_people > 0:
            # Somebody is there and it is not them. Worse than an empty chair, and reported
            # as its own state so §28 can lock rather than merely degrade.
            return Presence.UNKNOWN_PERSON
        return Presence.ABSENT

    def away_for(self, *, user_id: str, now: datetime | None = None) -> timedelta:
        now = now or datetime.now(UTC)
        seen = self._last_seen.get(user_id)
        if seen is None:
            return AWAY_BEFORE_LOCK * 10  # never seen: as away as it gets
        return now - seen

    def others_present(self, *, user_id: str) -> int:
        """How many people are here who are not this person (§29).

        Counts unrecognised faces *and* other recognised users: an authorised colleague is
        still somebody who should not hear the owner's email read aloud.
        """
        last = self._last
        if last is None:
            return 0
        return last.unknown_people + len(last.recognised - {user_id})


@dataclass
class SessionGuard:
    """Applies presence to a session, continuously (§28, §80, §81).

    The two halves of §28 — degrade, then lock — are separate because they have different
    remedies. A degraded session is repaired by the owner glancing at the camera; a locked
    one needs them to authenticate again. Collapsing them means either locking too eagerly
    (and being switched off) or degrading forever (and never locking).
    """

    monitor: PresenceMonitor

    def apply(
        self, session: AuthenticationSession, *, now: datetime | None = None
    ) -> tuple[AuthLevel, str]:
        """Update the session from what the room looks like, and say what changed.

        Returns the level the session is now worth. Mutates `present` on the session, which
        is what makes `effective_level` fall — the arithmetic lives in one place (Sprint 73)
        and this decides the input to it.
        """
        now = now or datetime.now(UTC)
        state = self.monitor.state(user_id=session.user_id, now=now)

        if state is Presence.UNKNOWN_PERSON:
            # §28 and §80 together. Not a degrade: somebody else is sitting there, and the
            # session must not be inheritable by whoever arrives next.
            session.present = False
            session.end("somebody else is at this machine")
            log.warning("session_locked_unknown_person", user=session.user_id)
            return AuthLevel.NONE, "somebody else is here"

        if state is Presence.ABSENT:
            away = self.monitor.away_for(user_id=session.user_id, now=now)
            session.present = False
            if away >= AWAY_BEFORE_LOCK:
                session.end("the owner has been away")
                log.info("session_locked_absent", user=session.user_id)
                return AuthLevel.NONE, "you have been away, so I locked this"
            # Degraded but recoverable. §28's "Authentication Level ลดลง", and the ceiling
            # rather than a collapse: stepping out of frame for a moment must not mean
            # authenticating again (§24), and must mean private things stop being available
            # before the lock arrives.
            session.present = True
            session.presence_cap = DEGRADED_TO
            return session.effective_level(now=now), "you stepped away"

        session.present = True
        session.presence_cap = None
        session.touch(now=now)
        return session.effective_level(now=now), ""

    def restore(
        self, session: AuthenticationSession, *, level: AuthLevel, now: datetime | None = None
    ) -> AuthLevel:
        """§81. The owner comes back and is recognised again.

        A restoration, not a new session: the same session id, so anything that was running
        continues and the audit trail stays one thread. But it needs a *fresh* identity check
        — `level` comes from a new fusion — because "they came back" is a claim about a person
        and the only thing that establishes a person is establishing them.
        """
        if session.expired(now=now):
            # Past the outer bound, this is a new session whatever the camera says.
            return AuthLevel.NONE
        session.verified(level=level, factors=session.factors, now=now)
        log.info("session_restored", user=session.user_id, level=int(level))
        return session.effective_level(now=now)


@dataclass(frozen=True)
class PrivacyState:
    """§29. Whether private things may be shown or spoken right now.

    Independent of authentication on purpose. The owner is perfectly identified and there is
    still somebody else in the room; that is not a fact about identity.
    """

    private_ok: bool
    others: int
    reason: str = ""

    def render(self) -> dict:
        return {"privacy_mode": not self.private_ok, "reason": self.reason}


def privacy_for(
    monitor: PresenceMonitor, *, user_id: str, now: datetime | None = None
) -> PrivacyState:
    """§29's four consequences, decided from one question: is anybody else here?"""
    others = monitor.others_present(user_id=user_id)
    if others > 0:
        return PrivacyState(
            private_ok=False,
            others=others,
            reason="มีคนอื่นอยู่ด้วย ผมจะไม่อ่านเรื่องส่วนตัวออกเสียงครับ",
        )
    return PrivacyState(private_ok=True, others=0)


#: §68's additions to the world state. Returned as a dict rather than written directly so the
#: world-state projector stays the only thing that writes it.
def world_fields(
    monitor: PresenceMonitor,
    session: AuthenticationSession | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What §68 asks the world state to carry.

    Note what is not here: no confidence, no template, no frame. The world state is read by
    the planner and reaches a model prompt, so anything in it is something a model sees (§9).
    """
    now = now or datetime.now(UTC)
    last = monitor._last
    level = session.effective_level(now=now) if session else AuthLevel.NONE
    user_id = session.user_id if session and level > AuthLevel.NONE else None
    return {
        "authenticated_user": user_id,
        "authentication_level": int(level),
        "authorized_users_present": sorted(last.recognised) if last else [],
        "unknown_people_present": last.unknown_people if last else 0,
        "privacy_mode": (
            not privacy_for(monitor, user_id=user_id, now=now).private_ok if user_id else False
        ),
        "last_identity_check": last.at.isoformat() if last else None,
    }
