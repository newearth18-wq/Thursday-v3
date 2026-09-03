"""What the owner already knows, and how much Thursday should still explain (§8, §9, §37–§45).

Everything else in the tutor reads this module and nothing else writes it. The spec asks for
two records — a capability profile and tutorial progress — and the interesting decisions are
about what is allowed to *move* them.

**A state only advances on evidence.** The tempting version advances on exposure: Thursday
mentioned the camera, so mark it DISCOVERED; the owner ran a file search, so mark file search
LEARNED. That produces a system which stops explaining things the owner never understood,
and the failure is silent — they simply stop being helped and cannot say why. So each
transition names what would justify it:

    NOT_DISCOVERED  → the owner has not been told this exists
    DISCOVERED      → Thursday has mentioned it
    TRIED           → the owner used it at least once
    LEARNED         → used it successfully, unprompted, more than once
    MASTERED        → used habitually and without a tip in a long while

MASTERED is deliberately hard to reach and impossible to reach quickly: it is the state that
*removes* help, so the bar for it is the highest and it is the one state that cannot be
granted by a single successful run. §45 lets the system infer verbosity from familiarity, and
this is where that inference stops being a guess.

**A downgrade is possible.** Somebody who has not used gestures in three months is not
MASTERED at gestures any more, whatever they once were. Skills decay, and a profile that only
climbs is a profile that eventually claims the owner knows everything.

**Teaching frequency is a ceiling, not a suggestion.** §39's OFF means off — no scoring, no
"important exception", no first-run intro. The one thing that is never suppressed is an answer
to a direct question (§32, §33), because that is not Thursday speaking up; it is Thursday
being asked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum

from thursday_core.logging import get_logger

log = get_logger(__name__)


class Familiarity(IntEnum):
    """How well the owner knows one capability (§8).

    Ordered, so "at least LEARNED" is a comparison rather than a set membership test.
    """

    NOT_DISCOVERED = 0
    DISCOVERED = 1
    TRIED = 2
    LEARNED = 3
    MASTERED = 4


class Verbosity(StrEnum):
    """How much Thursday explains while it works (§43–§45)."""

    BEGINNER = "BEGINNER"
    INTERMEDIATE = "INTERMEDIATE"
    EXPERT = "EXPERT"


class TeachingFrequency(IntEnum):
    """§7 and §39. Ordered so a threshold comparison replaces a lookup table."""

    OFF = 0
    #: "Only when asked" — §39's third option. Nothing unsolicited, ever.
    ON_REQUEST = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4


class TutorialStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


#: Successful, unprompted uses before a capability counts as LEARNED. Two rather than one:
#: the first success may have been Thursday's tip read aloud, and a tutorial that stops
#: helping after one lucky run is one that stops too early.
USES_FOR_LEARNED = 2

#: Uses before MASTERED, plus `MASTERY_QUIET` with no help needed. High on purpose — this
#: is the state that removes explanation, so it is the expensive one to reach.
USES_FOR_MASTERED = 6
MASTERY_QUIET = timedelta(days=7)

#: With no use for this long, familiarity decays one step. A profile that only ever climbs
#: eventually claims the owner is expert at everything, including what they last touched in
#: March.
DECAY_AFTER = timedelta(days=90)


@dataclass
class CapabilityKnowledge:
    """What the owner knows about one capability, and the evidence for saying so."""

    capability: str
    state: Familiarity = Familiarity.NOT_DISCOVERED
    #: Successful unprompted uses. The evidence behind TRIED/LEARNED/MASTERED.
    uses: int = 0
    #: Times Thursday introduced it. Evidence for DISCOVERED and nothing more — being told
    #: about a thing is not knowing it, and conflating the two is how a tutor goes quiet on
    #: somebody who never understood.
    introductions: int = 0
    first_seen: datetime | None = None
    last_used: datetime | None = None
    last_taught: datetime | None = None
    #: The owner said "not interested". Kept separate from `state`: declining to learn
    #: gestures is not the same as not knowing they exist, and re-offering a dismissed tip
    #: is the §66 failure.
    dismissed: bool = False

    def decayed(self, *, now: datetime) -> Familiarity:
        """Familiarity as of now, with disuse taken into account."""
        if self.state <= Familiarity.TRIED or self.last_used is None:
            return self.state
        if now - self.last_used < DECAY_AFTER:
            return self.state
        return Familiarity(self.state - 1)


@dataclass
class TutorialProgress:
    """§9. One tutorial, one owner, and where they got to."""

    tutorial_id: str
    status: TutorialStatus = TutorialStatus.NOT_STARTED
    current_step: int = 0
    completed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_activity: datetime | None = None
    #: How well it went, 0–1: completed steps over attempted ones. Reported rather than
    #: scored — this is not a grade, it is whether the lesson worked.
    confidence: float = 0.0

    @property
    def finished(self) -> bool:
        return self.status in (TutorialStatus.COMPLETED, TutorialStatus.SKIPPED)

    def _touch(self, now: datetime) -> None:
        self.last_activity = now
        attempted = len(self.completed_steps) + len(self.skipped_steps)
        self.confidence = (len(self.completed_steps) / attempted) if attempted else 0.0


class LearningRecord:
    """The owner's learning state. Read by everything in the tutor, written only here.

    Single-owner like the rest of Thursday (`Settings.owner_id`); the DB table carries a
    `user_id` column because the schema is shared, not because there are two people.
    """

    def __init__(self, *, frequency: TeachingFrequency = TeachingFrequency.NORMAL) -> None:
        self.frequency = frequency
        #: Set by the owner in Settings, overriding what familiarity would infer (§45:
        #: "ผู้ใช้ override ได้"). None means "let Thursday work it out".
        self.verbosity_override: Verbosity | None = None
        self._known: dict[str, CapabilityKnowledge] = {}
        self._tutorials: dict[str, TutorialProgress] = {}

    # ------------------------------------------------------------------ reading

    def knows(self, capability: str, *, now: datetime | None = None) -> Familiarity:
        entry = self._known.get(capability)
        if entry is None:
            return Familiarity.NOT_DISCOVERED
        return entry.decayed(now=now or datetime.now(UTC))

    def entry(self, capability: str) -> CapabilityKnowledge:
        """The full record, created on first ask. Callers that only need the level use
        `knows` — this is for the tip engine, which needs the evidence too."""
        return self._known.setdefault(capability, CapabilityKnowledge(capability=capability))

    def progress(self, tutorial_id: str) -> TutorialProgress:
        return self._tutorials.setdefault(tutorial_id, TutorialProgress(tutorial_id=tutorial_id))

    def all_capabilities(self) -> dict[str, CapabilityKnowledge]:
        return dict(self._known)

    def all_progress(self) -> dict[str, TutorialProgress]:
        return dict(self._tutorials)

    # ------------------------------------------------------------------ writing

    def introduced(self, capability: str, *, now: datetime | None = None) -> Familiarity:
        """Thursday mentioned it. Advances to DISCOVERED and **no further**.

        The ceiling is the point. Being told a feature exists is evidence about what
        Thursday said, not about what the owner can do.
        """
        now = now or datetime.now(UTC)
        entry = self.entry(capability)
        entry.introductions += 1
        entry.last_taught = now
        if entry.first_seen is None:
            entry.first_seen = now
        if entry.state is Familiarity.NOT_DISCOVERED:
            entry.state = Familiarity.DISCOVERED
        return entry.state

    def used(self, capability: str, *, ok: bool = True, now: datetime | None = None) -> Familiarity:
        """The owner actually used it. The only thing that can reach LEARNED or MASTERED.

        A failed attempt still counts as TRIED — they tried — but never counts toward the
        thresholds above it, because a capability somebody keeps failing at is the last one
        Thursday should go quiet about.
        """
        now = now or datetime.now(UTC)
        entry = self.entry(capability)
        if entry.first_seen is None:
            entry.first_seen = now
        entry.last_used = now

        if not ok:
            entry.state = max(entry.state, Familiarity.TRIED)
            return entry.state

        entry.uses += 1
        entry.state = max(entry.state, Familiarity.TRIED)
        if entry.uses >= USES_FOR_LEARNED:
            entry.state = max(entry.state, Familiarity.LEARNED)
        if entry.uses >= USES_FOR_MASTERED and self._quiet_for(entry, now):
            entry.state = Familiarity.MASTERED
        return entry.state

    def dismissed(self, capability: str, *, now: datetime | None = None) -> None:
        """The owner said no. §66: do not keep asking."""
        entry = self.entry(capability)
        entry.dismissed = True
        entry.last_taught = now or datetime.now(UTC)
        log.info("teaching_dismissed", capability=capability)

    @staticmethod
    def _quiet_for(entry: CapabilityKnowledge, now: datetime) -> bool:
        """Whether Thursday has managed to stay out of the way for long enough.

        MASTERED means the owner does this without help. If Thursday explained it this week,
        that is not yet true however many times they have used it.
        """
        return entry.last_taught is None or (now - entry.last_taught) >= MASTERY_QUIET

    # ------------------------------------------------------------------ tutorials

    def start(self, tutorial_id: str, *, now: datetime | None = None) -> TutorialProgress:
        now = now or datetime.now(UTC)
        progress = self.progress(tutorial_id)
        if progress.status is TutorialStatus.NOT_STARTED:
            progress.started_at = now
        progress.status = TutorialStatus.IN_PROGRESS
        progress._touch(now)
        return progress

    def advance(
        self, tutorial_id: str, step: str, *, now: datetime | None = None
    ) -> TutorialProgress:
        now = now or datetime.now(UTC)
        progress = self.progress(tutorial_id)
        if step not in progress.completed_steps:
            progress.completed_steps.append(step)
        progress.current_step = len(progress.completed_steps) + len(progress.skipped_steps)
        progress.status = TutorialStatus.IN_PROGRESS
        progress._touch(now)
        return progress

    def skip_step(
        self, tutorial_id: str, step: str, *, now: datetime | None = None
    ) -> TutorialProgress:
        now = now or datetime.now(UTC)
        progress = self.progress(tutorial_id)
        if step not in progress.skipped_steps:
            progress.skipped_steps.append(step)
        progress.current_step = len(progress.completed_steps) + len(progress.skipped_steps)
        progress._touch(now)
        return progress

    def complete(self, tutorial_id: str, *, now: datetime | None = None) -> TutorialProgress:
        now = now or datetime.now(UTC)
        progress = self.progress(tutorial_id)
        progress.status = TutorialStatus.COMPLETED
        progress.completed_at = now
        progress._touch(now)
        log.info("tutorial_completed", tutorial=tutorial_id, confidence=progress.confidence)
        return progress

    def skip(self, tutorial_id: str, *, now: datetime | None = None) -> TutorialProgress:
        now = now or datetime.now(UTC)
        progress = self.progress(tutorial_id)
        progress.status = TutorialStatus.SKIPPED
        progress._touch(now)
        return progress

    # ------------------------------------------------------------------ verbosity

    def verbosity(self, *, now: datetime | None = None) -> Verbosity:
        """How much to explain, inferred unless the owner has said (§45).

        Inferred from the *breadth* of what they have learned rather than from any one
        capability: somebody fluent with files and new to everything else is not an expert,
        and treating them as one is how a system stops explaining the parts they need.
        """
        if self.verbosity_override is not None:
            return self.verbosity_override
        now = now or datetime.now(UTC)
        levels = [entry.decayed(now=now) for entry in self._known.values()]
        learned = sum(1 for level in levels if level >= Familiarity.LEARNED)
        mastered = sum(1 for level in levels if level >= Familiarity.MASTERED)
        if mastered >= 3 and learned >= 5:
            return Verbosity.EXPERT
        if learned >= 2:
            return Verbosity.INTERMEDIATE
        return Verbosity.BEGINNER

    def may_teach_unprompted(self) -> bool:
        """§7/§39. Whether Thursday may bring teaching up on its own at all.

        A ceiling rather than an input to a score: OFF and ON_REQUEST mean no unsolicited
        teaching, and no relevance number can outvote that. Answering a direct question is
        not covered here — that is not Thursday speaking up.
        """
        return self.frequency >= TeachingFrequency.LOW

    # ------------------------------------------------------------------ §38 reset

    def reset_tips(self) -> int:
        """ "Show beginner tips again" — forget what has been *dismissed* and taught, keep
        what the owner has actually done."""
        touched = 0
        for entry in self._known.values():
            if entry.dismissed or entry.introductions:
                entry.dismissed = False
                entry.introductions = 0
                entry.last_taught = None
                touched += 1
        return touched

    def reset_tutorials(self) -> int:
        """ "Forget tutorial progress". Lessons only; the capability profile is what the
        owner *did*, and forgetting that would be forgetting their history, not their
        lessons."""
        count = len(self._tutorials)
        self._tutorials.clear()
        return count

    def reset_all(self) -> None:
        """ "Restart introduction" — back to a machine that has never met anybody."""
        self._known.clear()
        self._tutorials.clear()
        log.info("learning_reset")

    # ------------------------------------------------------------------ §42 display

    def snapshot(self, *, now: datetime | None = None) -> dict:
        """The light progress view. No score, no total, nothing to accumulate (§42)."""
        now = now or datetime.now(UTC)
        used = sorted(c for c, e in self._known.items() if e.decayed(now=now) >= Familiarity.TRIED)
        return {
            "verbosity": self.verbosity(now=now).value,
            "teaching": self.frequency.name,
            "used": used,
            "tutorials_completed": sorted(
                t for t, p in self._tutorials.items() if p.status is TutorialStatus.COMPLETED
            ),
        }
