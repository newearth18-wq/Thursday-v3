"""Noticing things nobody asked about (§46, §67, V10).

This is the file where Thursday stops being a thing you talk to and starts being a thing
that talks to you, and it is therefore the file most able to make Thursday unbearable — or
worse, dangerous. The spec's own framing is the constraint: *"Thursday เริ่มทำหน้าที่เป็น
ผู้ช่วยเชิงรุก แต่ห้ามกลายเป็น autonomous system ที่ทำอะไรก็ได้เอง"* — proactive, and not a
system that does whatever it likes.

So the whole design rests on one distinction: **noticing is not doing.**

An observer here produces an `Observation` — a thing that appears to be true and might
matter. An observation is not an action, not a plan, and not permission to take one. What it
becomes depends on a single test, applied in one place (`Observation.may_act_alone`):

    read-only  ·  reversible  ·  nothing leaves the machine   →   may just be done
    anything else                                             →   must be offered

That test is deliberately hard to pass. "Read the calendar to see what is coming" passes.
"Draft the document you will obviously need" does not — not because drafting is dangerous,
but because a draft the owner did not ask for is a file they did not expect, and the
difference between a helpful assistant and an alarming one is entirely in that gap.

The second constraint is volume. An assistant that is right nine times out of ten and speaks
forty times a day is one people turn off, and an assistant that has been turned off has a
safety record of zero. `ProactivityGate` already rate-limits and respects owner status; what
this module adds is *deduplication over time* — the same observation, seen every minute by a
worker loop, is one thing worth mentioning once, not sixty.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import NotificationPriority, RiskLevel, risk_at_least
from thursday_shared.ids import new_id

log = get_logger(__name__)

#: How long the same observation stays "already mentioned". A meeting tomorrow is still true
#: in five minutes, and saying so again is not new information — it is nagging.
REPEAT_WINDOW = timedelta(hours=6)

#: The event kinds V10 names. Listed rather than free-form so that a typo in an observer is
#: a failed lookup at import time rather than an event nobody ever receives.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        "calendar.upcoming",
        "email.received",
        "task.deadline",
        "task.completed",
        "device.offline",
        "file.changed",
        "project.blocked",
        "automation.triggered",
        "system.warning",
    }
)


@dataclass
class Observation:
    """Something Thursday noticed. Not a decision to act on it.

    The three flags are the whole safety model of this module, and they describe the
    *action being contemplated*, not the noticing. Reading a calendar to discover a meeting
    is always fine; what `read_only` describes is whether the thing Thursday would then do
    reads or writes.
    """

    kind: str
    summary: str
    #: What Thursday would do about it, in the owner's words. Empty when there is nothing
    #: to do and this is purely information.
    proposal: str = ""
    priority: NotificationPriority = NotificationPriority.NORMAL
    risk: RiskLevel = RiskLevel.LOW
    #: True when acting would only read. A draft is *not* read-only: it creates a file.
    read_only: bool = True
    #: True when acting could be undone with no trace left behind.
    reversible: bool = True
    #: True when acting would touch something outside this machine — a message, a booking,
    #: an API call. The single most important flag here.
    external: bool = False
    #: Whether the content is private, so the gate can hold it when others are present.
    private: bool = False
    #: What the offer would turn into, if accepted. Free-form and read by the offer layer.
    action: dict[str, Any] = field(default_factory=dict)
    id: UUID = field(default_factory=new_id)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Stable across repeated sightings of the same fact, so it can be deduplicated.
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = f"{self.kind}:{self.summary}"

    @property
    def may_act_alone(self) -> bool:
        """Whether Thursday may simply do this without asking.

        Every clause has to hold. This is the one place the question is answered, so that
        "safe action" cannot come to mean different things in different observers — which
        is how a system that was careful in nine places becomes uncontrolled in the tenth.
        """
        return (
            self.read_only
            and self.reversible
            and not self.external
            and not risk_at_least(self.risk, RiskLevel.MEDIUM)
        )

    def describe(self, language: str = "th") -> str:
        if not self.proposal:
            return self.summary
        if language == "th":
            return f"{self.summary} ต้องการให้ผม{self.proposal}ไหมครับ"
        return f"{self.summary} Would you like me to {self.proposal}?"


class ProactiveEngine:
    """Runs the observers, decides what is worth saying, and says it at most once.

    It holds no ability to act. What comes out is a list of observations for the offer layer
    to turn into questions — which keeps "what did Thursday notice" and "what did Thursday
    do about it" as separate, separately-testable things.
    """

    def __init__(self, *, gate: Any, repeat_window: timedelta = REPEAT_WINDOW) -> None:
        self._gate = gate
        self._observers: list[tuple[str, Callable[[], Any]]] = []
        self._mentioned: dict[str, datetime] = {}
        self._window = repeat_window

    def observe(self, name: str, observer: Callable[[], Any]) -> None:
        """Register something that looks for a condition and returns observations."""
        self._observers.append((name, observer))

    async def sweep(
        self,
        *,
        owner_status: str = "available",
        people_present: int = 1,
        now: datetime | None = None,
    ) -> list[Observation]:
        """Run every observer and return what is worth raising, now.

        One observer raising is never allowed to stop the others: a proactive layer that
        goes quiet because one check threw is a proactive layer that fails exactly when
        something is wrong, which is when it is most needed.
        """
        now = now or datetime.now(UTC)
        raised: list[Observation] = []

        for name, observer in self._observers:
            try:
                found = observer()
                if hasattr(found, "__await__"):
                    found = await found
            except Exception as exc:
                log.warning("observer_failed", observer=name, error=str(exc))
                continue

            for observation in found or []:
                if self._recently_mentioned(observation, now):
                    continue
                allowed, reason = self._gate.allows(
                    observation.priority,
                    owner_status=owner_status,
                    people_present=people_present,
                    private=observation.private,
                    now=now,
                )
                if not allowed:
                    log.debug("observation_held", kind=observation.kind, reason=reason)
                    continue
                self._gate.record(observation.priority, now=now)
                self._mentioned[observation.fingerprint] = now
                raised.append(observation)

        return raised

    def _recently_mentioned(self, observation: Observation, now: datetime) -> bool:
        """The same fact, said again, is not new information — it is nagging."""
        last = self._mentioned.get(observation.fingerprint)
        return last is not None and now - last < self._window

    def forget(self, fingerprint: str) -> None:
        """Allow something to be raised again — used when the owner acts on it."""
        self._mentioned.pop(fingerprint, None)


# --------------------------------------------------------------------------- the observers


def upcoming_meetings(
    calendar: Any,
    *,
    has_preparation: Callable[[Any], bool],
    within: timedelta = timedelta(days=1),
) -> Callable[[], Any]:
    """ "There is a meeting tomorrow and I cannot find anything prepared for it."

    The V10 acceptance case. Note what the observation *is*: a statement about the calendar
    and the absence of a document. Preparing that document is `external=False` but decidedly
    not `read_only`, so it is offered rather than done — the owner finding a file they did
    not ask for is a worse first experience of a proactive assistant than being asked.
    """

    async def observe() -> list[Observation]:
        now = datetime.now(UTC)
        events = await calendar.events(start=now, end=now + within)
        found: list[Observation] = []
        for event in events:
            if has_preparation(event):
                continue
            found.append(
                Observation(
                    kind="calendar.upcoming",
                    summary=(
                        f"พรุ่งนี้มีประชุม {event.title} เวลา {event.start:%H:%M} "
                        "และยังไม่พบเอกสารเตรียมประชุม"
                    ),
                    proposal="จัดเตรียมให้",
                    priority=NotificationPriority.IMPORTANT,
                    # Writing a document is not read-only, so this is an offer.
                    read_only=False,
                    reversible=True,
                    external=False,
                    private=True,
                    action={
                        "kind": "prepare_meeting",
                        "event_id": str(event.id),
                        "title": event.title,
                        "starts": event.start.isoformat(),
                        "attendees": list(event.attendees),
                    },
                    fingerprint=f"calendar.upcoming:{event.id}",
                )
            )
        return found

    return observe


def approaching_deadlines(
    tasks: Any, *, within: timedelta = timedelta(days=1)
) -> Callable[[], Any]:
    """ "That report is due tomorrow and one part is still open."

    Purely informational — there is no proposal, because the thing to do about a deadline is
    a decision only the owner can take. An assistant that offered to "finish it for you"
    would be offering something it cannot deliver.
    """

    def observe() -> list[Observation]:
        now = datetime.now(UTC)
        found: list[Observation] = []
        for task in tasks.list():
            if task.deadline is None or task.status.is_terminal:
                continue
            if now <= task.deadline <= now + within:
                hours = (task.deadline - now).total_seconds() / 3600
                found.append(
                    Observation(
                        kind="task.deadline",
                        summary=(
                            f"งาน “{task.title}” ครบกำหนดในอีก {hours:.0f} ชั่วโมง "
                            f"และยังอยู่ที่ {task.progress:.0%}"
                        ),
                        priority=NotificationPriority.IMPORTANT,
                        private=True,
                        fingerprint=f"task.deadline:{task.id}",
                    )
                )
        return found

    return observe


def offline_devices(hub: Any) -> Callable[[], Any]:
    """A device that was there and is not.

    Reconnecting is in V10's allowed self-recovery list, so this one *is* actionable without
    asking — but the reconnection lives in the recovery module, not here. This observer says
    what it sees.
    """

    def observe() -> list[Observation]:
        return [
            Observation(
                kind="device.offline",
                summary=f"{device.name} ออฟไลน์อยู่",
                priority=NotificationPriority.NORMAL,
                fingerprint=f"device.offline:{device.id}",
            )
            for device in hub.all()
            if str(device.status) == "offline"
        ]

    return observe


def anomalies_in(result: dict[str, Any], *, task_title: str = "") -> list[Observation]:
    """ "The data agent finished, but three rows look wrong."

    From the spec's own examples. Worth having as a function rather than a standing observer
    because it runs against one result at the moment it is produced, and because the thing
    it reports — a completed job with a caveat — is the case most likely to be skimmed past
    if it is only ever a field in a payload.
    """
    rows = result.get("rows")
    if not isinstance(rows, list) or not rows:
        return []

    suspect = [row for row in rows if _looks_wrong(row)]
    if not suspect:
        return []
    return [
        Observation(
            kind="task.completed",
            summary=(f"{task_title or 'งานที่เพิ่งเสร็จ'} เสร็จแล้ว แต่พบข้อมูลผิดปกติ {len(suspect)} รายการ"),
            priority=NotificationPriority.IMPORTANT,
            private=True,
            action={"kind": "show_rows", "rows": suspect[:10]},
            fingerprint=f"task.completed.anomaly:{task_title}:{len(suspect)}",
        )
    ]


def _looks_wrong(row: Any) -> bool:
    """A cheap, explainable anomaly test: empty or negative where a number is expected.

    Deliberately not a statistical outlier detector. "This row is 2.7 standard deviations
    from the mean" is a claim the owner cannot check at a glance, and a proactive message
    they cannot check is one they learn to ignore. A blank cell is a blank cell.
    """
    if not isinstance(row, dict):
        return False
    for value in row.values():
        if value is None or value == "":
            return True
        if isinstance(value, int | float) and not isinstance(value, bool) and value < 0:
            return True
    return False
