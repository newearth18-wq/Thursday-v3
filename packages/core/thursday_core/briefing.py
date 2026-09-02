"""The morning brief, the end of day, and the decision journal (§46, V10).

Three things that look like reporting features and are really the same feature: making
Thursday's own behaviour reviewable by the person it belongs to.

The brief and the end-of-day summary answer "what is coming" and "what happened". Both are
assembled from state that already exists — calendar, tasks, approvals, agent results,
health — rather than from a model, because a summary of the day that hallucinates one item
is a summary nobody can use for anything. Every line in both is a fact with a source.

The **decision journal** is the one that matters most and is easiest to skip. Over months, a
system with memory, learned skills and standing automations accumulates a great many choices
that nobody remembers making. "Why does Thursday always do X" becomes unanswerable, and an
assistant whose behaviour cannot be explained is one that has to be trusted blindly or not
at all. So a decision is recorded with its *alternatives* — the options not taken are what
turn a log line into something a person can actually re-decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_shared.enums import TaskState
from thursday_shared.ids import new_id

from thursday_core.logging import get_logger

log = get_logger(__name__)


@dataclass
class JournalEntry:
    """One decision, and enough context to disagree with it later."""

    id: UUID = field(default_factory=new_id)
    decision: str = ""
    reason: str = ""
    #: What was not chosen. Without this a journal is a log: it records the outcome and
    #: loses the only thing that makes it re-examinable.
    alternatives: list[str] = field(default_factory=list)
    impact: str = ""
    #: Who or what decided — "owner", an agent name, "policy". A decision whose author is
    #: unknown cannot be weighed against a later one.
    source: str = "thursday"
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_id: UUID | None = None
    project_id: UUID | None = None

    def describe(self, language: str = "th") -> str:
        alternatives = ", ".join(self.alternatives) or ("ไม่มี" if language == "th" else "none")
        if language == "th":
            return (
                f"{self.at:%Y-%m-%d} — {self.decision} (เพราะ {self.reason}; "
                f"ทางเลือกอื่น: {alternatives}; โดย {self.source})"
            )
        return (
            f"{self.at:%Y-%m-%d} — {self.decision} (because {self.reason}; "
            f"alternatives: {alternatives}; by {self.source})"
        )


class DecisionJournal:
    """Every choice worth being able to revisit."""

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    def record(
        self,
        decision: str,
        *,
        reason: str,
        alternatives: list[str] | None = None,
        impact: str = "",
        source: str = "thursday",
        task_id: UUID | None = None,
        project_id: UUID | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            decision=decision,
            reason=reason,
            alternatives=list(alternatives or []),
            impact=impact,
            source=source,
            task_id=task_id,
            project_id=project_id,
        )
        self._entries.append(entry)
        log.info("decision_recorded", decision=decision[:60], source=source)
        return entry

    def entries(
        self, *, since: datetime | None = None, source: str | None = None, limit: int = 50
    ) -> list[JournalEntry]:
        rows = [
            e
            for e in self._entries
            if (since is None or e.at >= since) and (source is None or e.source == source)
        ]
        return sorted(rows, key=lambda e: e.at, reverse=True)[:limit]

    def for_task(self, task_id: UUID) -> list[JournalEntry]:
        return [e for e in self._entries if e.task_id == task_id]

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class Brief:
    """A day, assembled. Every line traceable to something that exists."""

    when: date
    calendar: list[str] = field(default_factory=list)
    deadlines: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.calendar,
                self.deadlines,
                self.approvals,
                self.results,
                self.issues,
                self.suggestions,
            )
        )

    def render(self, language: str = "th") -> str:
        """Plain text, sections omitted when empty.

        An empty section printed as "Deadlines: none" every morning is a line people learn
        to skip, and the skipping generalises to the section that one day is not empty.
        """
        if self.empty:
            return "ไม่มีอะไรต้องรายงานครับ" if language == "th" else "Nothing to report."

        headings = (
            {
                "calendar": "ปฏิทิน",
                "deadlines": "กำหนดส่ง",
                "approvals": "รออนุมัติ",
                "results": "ผลงานที่เสร็จ",
                "issues": "ปัญหาระบบ",
                "suggestions": "ข้อเสนอ",
            }
            if language == "th"
            else {
                "calendar": "Calendar",
                "deadlines": "Deadlines",
                "approvals": "Waiting on you",
                "results": "Finished",
                "issues": "System",
                "suggestions": "Suggestions",
            }
        )
        lines: list[str] = []
        for key, heading in headings.items():
            items = getattr(self, key)
            if items:
                lines.append(f"{heading}:")
                lines += [f"  · {item}" for item in items]
        return "\n".join(lines)


class Briefer:
    """Assembles the brief and the end-of-day summary from state that already exists.

    No model. A summary of the day that invents one item is a summary nobody can use for
    anything, and the value of these is entirely that they can be trusted at a glance.
    """

    def __init__(
        self,
        *,
        tasks: Any,
        approvals: Any,
        calendar: Any = None,
        #: A zero-argument awaitable returning health checks — the container's own.
        health: Any = None,
        offers: Any = None,
        journal: DecisionJournal | None = None,
        memory: Any = None,
        skills: Any = None,
    ) -> None:
        self._tasks = tasks
        self._approvals = approvals
        self._calendar = calendar
        self._health = health
        self._offers = offers
        self._journal = journal
        self._memory = memory
        self._skills = skills

    async def morning(self, *, now: datetime | None = None) -> Brief:
        """What is coming, and what is waiting on the owner."""
        now = now or datetime.now(UTC)
        brief = Brief(when=now.date())

        if self._calendar is not None:
            events = await self._calendar.events(start=now, end=now + timedelta(days=1))
            brief.calendar = [e.describe("th") for e in events]

        horizon = now + timedelta(days=2)
        brief.deadlines = [
            f"{t.title} — {t.deadline:%d %b %H:%M} ({t.progress:.0%})"
            for t in self._tasks.list()
            if t.deadline and not t.status.is_terminal and t.deadline <= horizon
        ]
        brief.approvals = [a.action for a in self._approvals.pending()]
        if self._offers is not None:
            brief.suggestions = [o.text for o in self._offers.pending(now=now)]
        if self._health is not None:
            # The container's own health surface, whatever shape it reports in. Only the
            # failures reach the brief: a morning list that says nine things are fine is a
            # list people stop reading, and the tenth is the one that mattered.
            for check in await self._health():
                ok = check["ok"] if isinstance(check, dict) else check.ok
                if not ok:
                    component = check["component"] if isinstance(check, dict) else check.name
                    detail = check["detail"] if isinstance(check, dict) else check.detail
                    brief.issues.append(f"{component}: {detail}")
        return brief

    async def end_of_day(self, *, now: datetime | None = None) -> Brief:
        """What happened, including the parts that did not go well.

        Blocked and failed work is reported alongside what finished. A summary listing only
        successes is a summary that makes a bad day look like a good one, which is precisely
        when somebody needs to know.
        """
        now = now or datetime.now(UTC)
        since = datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)
        brief = Brief(when=now.date())

        today = [t for t in self._tasks.list(limit=200) if t.updated_at >= since]
        brief.results = [f"{t.title} — เสร็จแล้ว" for t in today if t.status is TaskState.COMPLETED]
        brief.issues = [
            f"{t.title} — {t.error or str(t.status)}"
            for t in today
            if t.status in (TaskState.FAILED, TaskState.BLOCKED)
        ]
        brief.deadlines = [
            f"{t.title} — ยังไม่เสร็จ ({t.progress:.0%})" for t in today if not t.status.is_terminal
        ]
        brief.approvals = [a.action for a in self._approvals.pending()]

        if self._journal is not None:
            brief.suggestions += [e.describe("th") for e in self._journal.entries(since=since)]
        if self._skills is not None:
            brief.suggestions += [
                f"สกิลใหม่ที่เรียนรู้ได้: {p.describe('th')}" for p in self._skills.unproposed()
            ]
        return brief
