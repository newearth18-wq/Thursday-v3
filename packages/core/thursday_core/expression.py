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

**Two axes, because §7 asks two questions** (Sprint 85).

The avatar addendum lists seventeen states — IDLE, LISTENING, THINKING, WORKING, SPEAKING,
SUCCESS, ERROR, WAITING_APPROVAL, SLEEP and the rest — as though they were one enum. They are
not. *How Thursday is going* and *what Thursday's body is doing* are independent facts, and
flattening them forces a choice that must not be made: a job that failed forty seconds ago
outranks everything on a single ordered table, so a Thursday that is **listening** while a
recent failure is still fresh would draw the failure and hide the microphone.

So `Mood` keeps its nine values and its ordering, and `Posture` is a second derived table
answering the other question. Both come out of the same `express()` call over the same
snapshot, so they cannot drift; neither is a blend of the other.

**And the microphone is not a state at all.** §10 requires that the avatar clearly indicate
microphone state. `Expression.listening` is therefore a plain boolean copied from the voice
loop, sitting outside both tables, because anything inside a priority table can be outranked
— and a recording indicator that a cheerier signal is allowed to hide is worse than no
indicator at all. `Posture.LISTENING` exists too, for the *pose*, and it genuinely can be
outranked: during barge-in the microphone is open while Thursday is still speaking, and the
body should show the speaking. The boolean is what stays true through that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from thursday_shared.models import WorldStateSnapshot

from thursday_core.plain import WORKING, Prop

#: How long a finished piece of work still colours the mood. Long enough that the owner
#: looking up a moment later sees what happened, short enough that Thursday does not sulk
#: about a failure from an hour ago.
FRESH = timedelta(seconds=45)

#: How long Thursday has to have been completely quiet before it is asleep rather than merely
#: idle (§20). Long enough that a pause for coffee is not a nap; short enough that a machine
#: left running overnight is not drawing a wide-awake robot at nobody.
DROWSY = timedelta(minutes=10)


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


class Posture(StrEnum):
    """What Thursday's body is doing right now (§8, §10–§12, §14, §20 — Sprint 85).

    Deliberately about *conduct*, not feeling: a posture is what an animator would need to
    know to pose the figure, and it stays the same whether the news is good or bad. The
    avatar addendum's own descriptions are body descriptions — head tilt and a hand near the
    chin for thinking, turn toward the owner and lean in for listening, a visor pulse for
    speaking — which is the clue that they were never moods.

    §19's `AUTHENTICATING` is **not here**, and its absence is deliberate. `IdentityGate` and
    `AuthenticationSession` exist in `thursday_security`, but nothing constructs them on the
    container, so there is no live "verification in flight" signal anywhere in a running
    Thursday. A member whose only reachable value is "never" is not a state, it is a claim —
    and this project has now shipped four of those and had to remove each one. It goes in
    when the identity layer is wired, and `tests/unit/test_expression.py` asserts it is
    absent until then so that adding the face without the signal fails loudly.
    """

    #: Composing or voicing a reply. §14: shown as a visor pulse, never a mouth.
    SPEAKING = "SPEAKING"
    #: A turn is in flight and Thursday has not started answering.
    THINKING = "THINKING"
    #: The microphone is capturing and nothing louder is happening.
    LISTENING = "LISTENING"
    #: Agents are running. §12: the motion should correspond to what kind of work it is.
    WORKING = "WORKING"
    #: Nothing at all has happened for `DROWSY`. §20.
    SLEEPING = "SLEEPING"
    #: Awake, attentive, nothing to do. §8: this must still breathe, blink and shift.
    STILL = "STILL"


#: Nearest the owner first. Speaking outranks working because a reply being delivered is the
#: thing the owner is in the middle of; listening outranks working for the same reason. Order
#: alone does most of the work: by the time `SLEEPING` is reached, every busier posture has
#: already been ruled out, so its own test is only about how long the quiet has lasted.
POSTURE_ORDER: tuple[Posture, ...] = (
    Posture.SPEAKING,
    Posture.THINKING,
    Posture.LISTENING,
    Posture.WORKING,
    Posture.SLEEPING,
    Posture.STILL,
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
    #: What the body is doing, independently of how it is going (Sprint 85).
    posture: Posture
    #: §10. Outside both tables on purpose: a privacy indicator that a priority order is
    #: allowed to hide is not an indicator. True exactly when the microphone is capturing.
    listening: bool
    #: The allowlisted phrase from `plain.activity`, or "" when nothing is running. Never an
    #: agent name — there is no code path here that has one.
    activity: str
    #: §13. What Thursday is holding, from `plain.prop` — the same closed vocabulary, keyed
    #: on the same capability as `activity`, so the picture and the caption can never
    #: describe different work. `NONE` whenever nothing is running.
    prop: Prop
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
            "posture": self.posture.value,
            "listening": self.listening,
            "activity": self.activity,
            "prop": self.prop.value,
            "because": self.because,
            "intensity": round(self.intensity, 3),
            "running": self.running,
            "waiting": self.waiting,
            "unhealthy": self.unhealthy,
        }


def _fresh(at: datetime | None, *, now: datetime) -> bool:
    return at is not None and (now - at) <= FRESH


def _prop(name: str) -> Prop:
    """The prop a snapshot names, or none at all.

    Permissive on purpose. The value arrives over the bus from an emitter that may be an
    older build, and refusing to describe Thursday's condition at all because a *drawing*
    was unrecognised would trade something that matters for something that does not.
    """
    try:
        return Prop(name)
    except ValueError:
        return Prop.NONE


def _quiet_since(at: datetime | None, *, now: datetime) -> bool:
    """Whether nothing has happened for long enough to call it sleep.

    `None` — no event ever seen, which is a Thursday that has only just started — counts as
    awake. The two errors are not symmetric: a robot that looks awake while idle is merely
    unremarkable, and one that looks asleep on a machine that is in fact working is a lie
    about what the owner's computer is doing.
    """
    return at is not None and (now - at) > DROWSY


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

    # The second axis. By the time SLEEPING is considered, every busier posture has been
    # ruled out by the ordering, so the only questions left are how long the quiet has run
    # and whether anything is still owed to the owner — a Thursday with a question waiting
    # must not doze off in front of it, however long it has been standing there.
    stands: dict[Posture, bool] = {
        Posture.SPEAKING: turn.speaking,
        Posture.THINKING: turn.thinking,
        Posture.LISTENING: turn.listening,
        Posture.WORKING: running > 0,
        Posture.SLEEPING: waiting == 0 and _quiet_since(world.last_event_at, now=moment),
        Posture.STILL: True,
    }
    posture = next(candidate for candidate in POSTURE_ORDER if stands[candidate])

    in_flight = running + waiting + unhealthy
    intensity = min(1.0, FLOOR[mood] + 0.12 * in_flight)

    return Expression(
        mood=mood,
        posture=posture,
        # Copied, never derived: this is the one field on the whole expression that no table
        # gets a vote on. If the microphone is open the owner is told so, whatever else
        # Thursday happens to be feeling about the last job.
        listening=turn.listening,
        # While work is running the owner is told what it is; otherwise the line is empty
        # rather than stale, because a leftover "กำลังค้นข้อมูล" under a finished job is a
        # lie that looks like a feature.
        activity=(world.current_activity or WORKING) if running else "",
        # Cleared with the activity, and for the same reason: a robot still holding a book
        # after the search finished is the same lie as a caption that says it is searching.
        # An unrecognised value collapses to NONE rather than raising — the wire is not the
        # place to be strict about a drawing.
        prop=_prop(world.current_prop) if running else Prop.NONE,
        because=BECAUSE[mood],
        intensity=intensity,
        running=running,
        waiting=waiting,
        unhealthy=unhealthy,
    )
