"""Ports for the things Thursday does not own (§15, V9).

A calendar lives in Google or Outlook or a phone. A message goes out through Gmail or LINE
or a corporate relay. Neither is Thursday's, and both are reached the same way everything
else external is: a Protocol here, a real adapter later, a local adapter now (ADR 0001).

The two protocols are shaped by one asymmetry that runs through this whole file. **Reading is
recoverable and sending is not.** A calendar read that returns the wrong week is a moment of
confusion. A message sent to the wrong person cannot be recalled, is already in their inbox,
and no amount of undo machinery gets it back. So `MessageProvider` separates `draft` from
`send`, and the communication agent only ever reaches for the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from thursday_shared.ids import new_id


@dataclass(frozen=True)
class CalendarEvent:
    """One entry. Times are timezone-aware, always — a naive datetime in a calendar is a
    meeting that happens at the wrong hour for somebody."""

    id: UUID = field(default_factory=new_id)
    title: str = ""
    start: datetime = field(default_factory=lambda: datetime.now(UTC))
    end: datetime | None = None
    location: str = ""
    attendees: tuple[str, ...] = ()
    notes: str = ""
    calendar: str = "default"

    @property
    def duration(self) -> timedelta:
        return (self.end - self.start) if self.end else timedelta(hours=1)

    def overlaps(self, other: CalendarEvent) -> bool:
        end, other_end = self.start + self.duration, other.start + other.duration
        return self.start < other_end and other.start < end

    def describe(self, language: str = "th") -> str:
        when = f"{self.start:%a %d %b %H:%M}"
        where = (
            f" ที่ {self.location}"
            if self.location and language == "th"
            else (f" at {self.location}" if self.location else "")
        )
        return f"{when} — {self.title}{where}"


@dataclass(frozen=True)
class Message:
    """Something to be sent, or something that was.

    ``sent_at`` is the only thing separating a draft from a sent message, and it is set by
    the provider at the moment of sending rather than by the caller. A field the caller
    could set is a field that gets set by accident.
    """

    id: UUID = field(default_factory=new_id)
    channel: str = "email"  # email | chat | sms
    to: tuple[str, ...] = ()
    subject: str = ""
    body: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    sent_at: datetime | None = None

    @property
    def is_draft(self) -> bool:
        return self.sent_at is None


@runtime_checkable
class CalendarProvider(Protocol):
    name: str
    local: bool

    async def events(
        self, *, start: datetime, end: datetime, calendar: str | None = None
    ) -> list[CalendarEvent]: ...

    async def create(self, event: CalendarEvent) -> CalendarEvent: ...


@runtime_checkable
class MessageProvider(Protocol):
    """Drafting and sending are separate methods on purpose.

    A provider that only implemented `send` would leave the caller no way to prepare
    something for a person to look at, which is the only safe way to compose an outbound
    message on somebody's behalf.
    """

    name: str
    local: bool

    async def draft(self, message: Message) -> Message: ...
    async def send(self, message_id: UUID) -> Message: ...
    async def outbox(self) -> list[Message]: ...


# --------------------------------------------------------------------------- local adapters


class LocalCalendar:
    """An in-process calendar. Real behaviour, no external account.

    Not a stub: it stores events, answers ranges, and detects conflicts, which is everything
    the agent above it needs to be exercised honestly. What it is not is *the owner's*
    calendar — see `docs/21-agents-and-skills.md` on what remains unbuilt.
    """

    name = "local"
    local = True

    def __init__(self) -> None:
        self._events: list[CalendarEvent] = []

    async def events(
        self, *, start: datetime, end: datetime, calendar: str | None = None
    ) -> list[CalendarEvent]:
        return sorted(
            (
                e
                for e in self._events
                if e.start < end
                and (e.start + e.duration) > start
                and (calendar is None or e.calendar == calendar)
            ),
            key=lambda e: e.start,
        )

    async def create(self, event: CalendarEvent) -> CalendarEvent:
        self._events.append(event)
        return event

    def conflicts(self, event: CalendarEvent) -> list[CalendarEvent]:
        return [e for e in self._events if e.id != event.id and e.overlaps(event)]


class LocalOutbox:
    """Drafts and an outbox, in process. Nothing leaves the machine.

    `send` marks a draft sent and returns it; it does not reach a network, and the module
    docstring in `communication.py` says so plainly rather than letting the method name
    imply otherwise.
    """

    name = "local"
    local = True

    def __init__(self) -> None:
        self._messages: dict[UUID, Message] = {}

    async def draft(self, message: Message) -> Message:
        self._messages[message.id] = message
        return message

    async def send(self, message_id: UUID) -> Message:
        message = self._messages.get(message_id)
        if message is None:
            raise KeyError(f"no draft {message_id}")
        # Stamped here, by the provider, at the moment of sending. A `sent_at` the caller
        # could pass in is one that gets passed in by accident.
        sent = Message(
            id=message.id,
            channel=message.channel,
            to=message.to,
            subject=message.subject,
            body=message.body,
            created_at=message.created_at,
            sent_at=datetime.now(UTC),
        )
        self._messages[message_id] = sent
        return sent

    async def outbox(self) -> list[Message]:
        return sorted(self._messages.values(), key=lambda m: m.created_at)

    def get(self, message_id: UUID) -> Message | None:
        return self._messages.get(message_id)


def parse_recipients(raw: Any) -> tuple[str, ...]:
    """Normalise whatever the caller supplied into a tuple of recipients.

    Empty when nothing usable was given, and the caller is expected to treat that as a
    refusal rather than a default. There is no sensible default recipient for a message.
    """
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    elif isinstance(raw, list | tuple | set):
        parts = [str(p).strip() for p in raw]
    else:
        return ()
    return tuple(p for p in parts if p)
