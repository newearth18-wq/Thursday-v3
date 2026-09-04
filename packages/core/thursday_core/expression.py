"""What Thursday shows about itself — Sprint 80.

The screen has to answer three questions at a glance: *is anything happening*, *how is it
going*, and *does it need me*. Sprint 65 already answered the first in plain language. This
module answers the other two, and it is built around one rule:

**A mood is derived, never set.**

There is no `set_mood`, no `mood=` parameter, and no way for a caller to assert how Thursday
feels — the same rule as `verified` in ADR 0012, and for the same reason. A mood that can be
assigned is a mood that will read CALM while the audit chain is broken, because the code path
that assigns it is not the code path that noticed. So the only way to produce an `Expression`
is `express()`, and the only thing `express()` reads is state somebody else already made true.

**This is Thursday's own state. It is never a reading of the person.**

§55 of the identity requirement forbids inferring emotion, health, religion, politics,
gender or personality from anybody. Nothing here comes close, and the guarantee is
structural rather than a promise: this module imports no camera, no microphone, no
biometric and no security package, so there is no path from a person to a mood. The inputs
are counts of Thursday's own work and booleans about Thursday's own output. `tests/unit/
test_expression.py` asserts the import list, so the guarantee fails loudly if somebody
later reaches for a face.

**Priority, not a blend.** The moods are an ordered table and the first match wins. Averaging
would let three cheerful signals wash out one failure, which is exactly the direction that
must never be possible: something that needs the owner outranks something that looks nice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from thursday_shared.models import WorldStateSnapshot

from thursday_core.plain import WORKING

#: How long a finished piece of work still colours the mood. Long enough that the owner
#: looking up a moment later sees what happened, short enough that Thursday does not sulk
#: about a failure from an hour ago.
FRESH = timedelta(seconds=45)


class Mood(StrEnum):
    """How Thursday is going. Nine states, because a person can read nine faces.

    Named for Thursday's condition rather than for a feeling word where the two differ:
    `WAITING` is not impatience, it is "this cannot proceed without you".
    """

    #: Everything is stopped. Not a mood so much as a fact, and it outranks every other.
    STOPPED = "STOPPED"
    #: A piece of work failed recently.
    FAILING = "FAILING"
    #: Part of Thursday is not working, even though the last job may have gone fine.
    CONCERNED = "CONCERNED"
    #: Something cannot proceed until the owner decides.
    WAITING = "WAITING"
    #: Work finished but its effect could not be observed (ADR 0012), or confidence was low.
    UNSURE = "UNSURE"
    #: Work is running now.
    WORKING = "WORKING"
    #: Work finished, verified, recently.
    PLEASED = "PLEASED"
    #: A turn is in flight — listening, thinking or speaking.
    ATTENTIVE = "ATTENTIVE"
    #: Nothing is happening and nothing is wrong.
    CALM = "CALM"


#: Most urgent first. `express()` walks this in order and takes the first match, so adding a
#: mood means deciding where it sits — there is no default position and no scoring to tune.
ORDER: tuple[Mood, ...] = (
    Mood.STOPPED,
    Mood.FAILING,
    Mood.CONCERNED,
    Mood.WAITING,
    Mood.UNSURE,
    Mood.WORKING,
    Mood.PLEASED,
    Mood.ATTENTIVE,
    Mood.CALM,
)

#: Why the mood is what it is, in one sentence, declared in advance. Not free text: this
#: reaches a screen, and Sprint 65's rule is that user-facing strings come from a table so
#: that an unrecognised state produces a vague sentence rather than an internal one.
BECAUSE: dict[Mood, str] = {
    Mood.STOPPED: "ทุกอย่างถูกสั่งหยุดไว้",
    Mood.FAILING: "งานล่าสุดทำไม่สำเร็จ",
    Mood.CONCERNED: "มีบางส่วนยังไม่พร้อมใช้งาน",
    Mood.WAITING: "รอคุณตัดสินใจอยู่",
    Mood.UNSURE: "ผมยังยืนยันผลลัพธ์ไม่ได้",
    Mood.WORKING: "กำลังทำงานให้อยู่",
    Mood.PLEASED: "งานล่าสุดเรียบร้อยดี",
    Mood.ATTENTIVE: "กำลังฟังอยู่",
    Mood.CALM: "ว่างอยู่ พร้อมรับงาน",
}

#: The least motion each mood is drawn with, before anything in flight is added. A floor
#: rather than a value: STOPPED must not animate calmly just because nothing is running.
FLOOR: dict[Mood, float] = {
    Mood.STOPPED: 0.0,
    Mood.FAILING: 0.55,
    Mood.CONCERNED: 0.45,
    Mood.WAITING: 0.5,
    Mood.UNSURE: 0.4,
    Mood.WORKING: 0.6,
    Mood.PLEASED: 0.35,
    Mood.ATTENTIVE: 0.45,
    Mood.CALM: 0.15,
}

#: Below this, an answer is reported as one Thursday is not sure of. Matches the threshold
#: the conversation view already uses to show a confidence figure at all.
SURE_ENOUGH = 0.7


@dataclass(frozen=True)
class Turn:
    """What the turn in flight looks like, if there is one.

    Every field is about Thursday's own output: whether it is composing, whether it managed
    to observe the effect of what it did, how confident the answer was. Nothing here
    describes the person, and there is no field in which anything about them would fit.
    """

    thinking: bool = False
    speaking: bool = False
    listening: bool = False
    #: ADR 0012. False when an action was dispatched but its effect could not be observed.
    verified: bool = True
    confidence: float = 1.0


@dataclass(frozen=True)
class Expression:
    """One derived view of Thursday's condition. Constructed only by `express()`."""

    mood: Mood
    #: The allowlisted phrase from `plain.activity`, or "" when nothing is running. Never an
    #: agent name — there is no code path here that has one.
    activity: str
    because: str
    #: 0–1. How much motion to draw with. Derived from the mood floor and what is in flight.
    intensity: float
    running: int = 0
    waiting: int = 0
    unhealthy: int = 0

    def payload(self) -> dict[str, object]:
        """The shape the socket and the HTTP endpoint both send. One shape, one producer."""
        return {
            "mood": self.mood.value,
            "activity": self.activity,
            "because": self.because,
            "intensity": round(self.intensity, 3),
            "running": self.running,
            "waiting": self.waiting,
            "unhealthy": self.unhealthy,
        }


def _fresh(at: datetime | None, *, now: datetime) -> bool:
    return at is not None and (now - at) <= FRESH


def express(
    world: WorldStateSnapshot,
    *,
    unhealthy: int,
    lockdown: bool,
    turn: Turn = Turn(),
    now: datetime | None = None,
) -> Expression:
    """Derive the current expression from state somebody else already made true.

    `unhealthy` and `lockdown` have no defaults on purpose. Neither is honestly available on
    the world snapshot, and a default would let a caller that forgot one report a calm
    Thursday with a dead database or a live emergency stop — the failure mode this project
    has now hit four times. Required arguments make a new caller answer the question.

    `lockdown` in particular is taken from `PermissionEngine.lockdown`, which is where the
    emergency stop actually sets it. `WorldStateSnapshot.lockdown` exists and is never
    written by anything, so a mood built on it would have been permanently false while
    claiming to show the loudest state Thursday has.
    """
    moment = now or datetime.now(UTC)
    running = sum(1 for state in world.running_agents.values() if state == "working")
    waiting = len(world.pending_approvals)

    holds: dict[Mood, bool] = {
        Mood.STOPPED: lockdown,
        Mood.FAILING: _fresh(world.last_failure_at, now=moment),
        Mood.CONCERNED: unhealthy > 0,
        Mood.WAITING: waiting > 0,
        Mood.UNSURE: not turn.verified or turn.confidence < SURE_ENOUGH,
        Mood.WORKING: running > 0,
        Mood.PLEASED: _fresh(world.last_success_at, now=moment),
        Mood.ATTENTIVE: turn.thinking or turn.speaking or turn.listening,
        Mood.CALM: True,
    }
    mood = next(candidate for candidate in ORDER if holds[candidate])

    in_flight = running + waiting + unhealthy
    intensity = min(1.0, FLOOR[mood] + 0.12 * in_flight)

    return Expression(
        mood=mood,
        # While work is running the owner is told what it is; otherwise the line is empty
        # rather than stale, because a leftover "กำลังค้นข้อมูล" under a finished job is a
        # lie that looks like a feature.
        activity=(world.current_activity or WORKING) if running else "",
        because=BECAUSE[mood],
        intensity=intensity,
        running=running,
        waiting=waiting,
        unhealthy=unhealthy,
    )
