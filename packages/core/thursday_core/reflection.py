"""Looking back at its own work, and learning from being corrected (§46, V10).

Two related things, and the second is where a system like this most easily goes wrong.

**Self-evaluation** asks, after a task: did it work, was the output accepted, did the owner
have to correct it, which agent did well, which skill should change. All of it is derived
from what already happened — the verification report, the retries, whether the owner came
back and rewrote the result — because a system that scores its own work by asking a model
how it did will report that it did well.

**Feedback learning** is the dangerous half, and the spec draws the line itself: *"ห้าม
เปลี่ยน permanent preference จากเหตุการณ์เดียวโดยไม่มี confidence"* — never change a permanent
preference from a single event without confidence.

The reason is worth stating plainly, because the shortcut is very tempting. The owner says
"แบบนี้ไม่เอา" once. It is trivial to write that down as a preference, and it feels
responsive. But a single "no" is ambiguous in a way a stored preference is not: it might
mean *never do this*, or *not for this document*, or *not today*, or *you misunderstood the
request*. Storing the strongest reading of an ambiguous signal produces an assistant that
progressively stops doing things for reasons nobody remembers, and — worse — the owner
cannot see what happened, because nothing announced itself.

So a correction becomes a `FeedbackEvent`, feedback accumulates, and only a *repeated,
consistent* signal is proposed as a preference. Proposed: even then it is a question, not a
write (PART 76 — an agent cannot write the owner's preferences).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_shared.enums import AgentVerdict, TaskState
from thursday_shared.ids import new_id

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: How many consistent corrections before the same complaint is worth proposing as a
#: standing preference. Three is a judgement, and the direction of the error matters more
#: than the number: proposing too late costs a repeated annoyance, proposing too early
#: writes a rule the owner never agreed to and cannot see.
CONFIDENCE_REPEATS = 3

#: Corrections older than this stop counting. A complaint from two months ago about a format
#: nobody uses any more should not be accumulating towards a permanent rule.
FEEDBACK_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class TaskReview:
    """What can be said about a finished task from the record it left."""

    task_id: UUID
    succeeded: bool
    verified: bool
    attempts: int = 1
    agent: str = ""
    #: True when the owner came back and changed the result — the strongest available
    #: signal that the output was not what they wanted, and one nobody has to be asked for.
    corrected: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Worked, was confirmed, first time, and nobody had to fix it afterwards."""
        return self.succeeded and self.verified and self.attempts == 1 and not self.corrected


@dataclass
class FeedbackEvent:
    """The owner saying this was not right. One data point, not a rule."""

    id: UUID = field(default_factory=new_id)
    subject: str = ""
    #: What they said, kept verbatim. A paraphrase of a complaint loses the thing that
    #: makes it comparable to the next one.
    said: str = ""
    task_id: UUID | None = None
    agent: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class PreferenceProposal:
    """A pattern in the corrections, put to the owner as a question."""

    subject: str
    occurrences: int
    examples: tuple[str, ...]

    def describe(self, language: str = "th") -> str:
        if language == "th":
            return (
                f"คุณแก้เรื่อง “{self.subject}” มา {self.occurrences} ครั้งแล้ว "
                "ต้องการให้ผมจำเป็นค่าตั้งต้นไหมครับ"
            )
        return (
            f"You have corrected “{self.subject}” {self.occurrences} times. "
            "Shall I remember that as a default?"
        )


class SelfEvaluator:
    """Reviews finished work, and keeps a running record of how each agent does."""

    def __init__(self) -> None:
        self._reviews: list[TaskReview] = []
        self._by_agent: dict[str, list[bool]] = defaultdict(list)

    def review(self, task: Any, *, corrected: bool = False) -> TaskReview:
        """Judge one finished task from its own record.

        Nothing here asks a model how it did. A system that scores its own work by
        generation will report that it did well, and the record — verification, retries,
        whether the owner rewrote the result — is both cheaper and true.
        """
        verification = getattr(task, "verification", None)
        plan = getattr(task, "plan", None)
        steps = getattr(plan, "steps", []) if plan else []
        attempts = max((getattr(s, "attempt", 1) for s in steps), default=1)
        agent = next(
            (s.name for s in reversed(steps) if getattr(s, "name", "")), task.assigned_agent or ""
        )

        notes: list[str] = []
        if verification is not None and verification.verdict is AgentVerdict.RETRY:
            notes.append("needed a retry before it passed")
        if attempts > 1:
            notes.append(f"took {attempts} attempts")
        if corrected:
            notes.append("the owner changed the result afterwards")

        review = TaskReview(
            task_id=task.id,
            succeeded=task.status is TaskState.COMPLETED,
            verified=bool(verification and verification.passed),
            attempts=attempts,
            agent=agent,
            corrected=corrected,
            notes=notes,
        )
        self._reviews.append(review)
        if agent:
            self._by_agent[agent].append(review.clean)
        log.debug("task_reviewed", task=str(task.id)[:8], clean=review.clean, agent=agent)
        return review

    def agent_scores(self) -> dict[str, float]:
        """Share of each agent's work that came out clean.

        A record rather than a ranking: an agent doing hard jobs will score below one doing
        easy jobs, and reading this as "which agent is best" would route work away from the
        agent that handles the difficult cases.
        """
        return {agent: sum(runs) / len(runs) for agent, runs in self._by_agent.items() if runs}

    def reviews(self, *, limit: int = 50) -> list[TaskReview]:
        return self._reviews[-limit:]


class FeedbackLog:
    """Corrections, and the confidence rule that stops one becoming a rule."""

    def __init__(
        self,
        *,
        repeats: int = CONFIDENCE_REPEATS,
        window: timedelta = FEEDBACK_WINDOW,
    ) -> None:
        self._events: list[FeedbackEvent] = []
        self._repeats = repeats
        self._window = window
        self._proposed: set[str] = set()

    def record(
        self,
        subject: str,
        *,
        said: str = "",
        task_id: UUID | None = None,
        agent: str = "",
        now: datetime | None = None,
    ) -> FeedbackEvent:
        """Note that the owner corrected something. Changes nothing on its own."""
        event = FeedbackEvent(
            subject=subject.strip().lower(),
            said=said,
            task_id=task_id,
            agent=agent,
            at=now or datetime.now(UTC),
        )
        self._events.append(event)
        log.info("feedback_recorded", subject=event.subject[:40], agent=agent)
        return event

    def events(self, *, subject: str | None = None) -> list[FeedbackEvent]:
        return [e for e in self._events if subject is None or e.subject == subject.strip().lower()]

    def proposals(self, *, now: datetime | None = None) -> list[PreferenceProposal]:
        """Subjects corrected often enough, and recently enough, to be worth asking about.

        This is the whole confidence rule, and it deliberately returns *proposals* rather
        than writing anything. Even at three consistent corrections the answer is a question:
        an agent may not write the owner's preferences (PART 76), and a preference the owner
        never agreed to is one they cannot find to change.
        """
        now = now or datetime.now(UTC)
        cutoff = now - self._window
        grouped: dict[str, list[FeedbackEvent]] = defaultdict(list)
        for event in self._events:
            if event.at >= cutoff:
                grouped[event.subject].append(event)

        return [
            PreferenceProposal(
                subject=subject,
                occurrences=len(events),
                examples=tuple(e.said for e in events[-3:] if e.said),
            )
            for subject, events in grouped.items()
            if len(events) >= self._repeats and subject not in self._proposed
        ]

    def mark_proposed(self, subject: str) -> None:
        self._proposed.add(subject.strip().lower())

    def __len__(self) -> int:
        return len(self._events)
