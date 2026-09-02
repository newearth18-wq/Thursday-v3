"""Calendar Agent (§15, V9).

Answers questions about the owner's time, and prepares — never commits — changes to it.

The split matters more here than it looks. Reading a calendar is harmless and constant:
"what have I got on Thursday", "am I free at three", "when did I last see them". Writing to
one is not, because a calendar entry is a promise to other people. Accepting a meeting sends
a notification. Moving one moves everybody. So `create` goes through the ordinary approval
path as an EXTERNAL action, and this agent's ceiling stops below it.

The one piece of real judgement it carries is **conflict detection**. An assistant that
schedules over an existing commitment has not helped; and the failure is quiet, because the
new entry looks perfectly fine on its own. So a proposed time is always checked against what
is already there, and a clash is reported as a clash rather than resolved silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent
from thursday_agents.ports import CalendarEvent, parse_recipients

#: How far ahead "what's coming up" looks when nobody said.
DEFAULT_HORIZON = timedelta(days=7)


class CalendarAgent(BaseAgent):
    spec = AgentSpec(
        name="calendar",
        description="Reads the calendar, finds free time, and prepares entries for approval.",
        capabilities=["calendar", "schedule", "availability", "meeting"],
        tools=[],
        agent_type="specialist",
        supported_input=["question", "start", "end"],
        supported_output=["events", "conflicts", "summary"],
        output_schema={"events": "list", "summary": "string"},
        # Reading time is READ. Creating an entry is EXTERNAL — it notifies other people —
        # and is deliberately above this ceiling, so it takes the approval path.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=0, usd=0.01),
        model_tier=ModelTier.FAST,
        cost_profile="cheap",
        latency_profile="fast",
        privacy_profile="local_preferred",
        system_prompt=(
            "You answer questions about the owner's calendar from the entries you are "
            "given. You never invent an appointment that is not there."
        ),
    )

    def __init__(self, calendar: Any) -> None:
        super().__init__()
        self._calendar = calendar

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        start = _as_datetime(contract.inputs.get("start")) or datetime.now(UTC)
        end = _as_datetime(contract.inputs.get("end")) or (start + DEFAULT_HORIZON)

        events = await self._calendar.events(start=start, end=end)
        rendered = [
            {
                "id": str(e.id),
                "title": e.title,
                "start": e.start.isoformat(),
                "end": (e.end or e.start + e.duration).isoformat(),
                "location": e.location,
                "attendees": list(e.attendees),
            }
            for e in events
        ]

        proposed = self._proposal(contract, start)
        conflicts: list[dict[str, Any]] = []
        if proposed is not None:
            clashing = [e for e in events if e.overlaps(proposed)]
            conflicts = [
                {"id": str(e.id), "title": e.title, "start": e.start.isoformat()} for e in clashing
            ]

        summary = self._summarise(rendered, proposed, conflicts)
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "events": rendered,
                "summary": summary,
                # Prepared, not created. The field is named for what happened.
                "proposed": _render(proposed) if proposed else None,
                "conflicts": conflicts,
                "created": False,
            },
            summary=summary,
            evidence=[{"window": [start.isoformat(), end.isoformat()], "events": len(rendered)}],
        )

    # ------------------------------------------------------------------ internals

    def _proposal(self, contract: JobContract, default_start: datetime) -> CalendarEvent | None:
        """An entry the caller asked to have prepared, if any."""
        title = str(contract.inputs.get("title") or "").strip()
        if not title:
            return None
        start = _as_datetime(contract.inputs.get("at")) or default_start
        minutes = int(contract.inputs.get("minutes") or 60)
        return CalendarEvent(
            title=title,
            start=start,
            end=start + timedelta(minutes=minutes),
            location=str(contract.inputs.get("location") or ""),
            attendees=parse_recipients(contract.inputs.get("attendees")),
        )

    def _summarise(
        self,
        events: list[dict[str, Any]],
        proposed: CalendarEvent | None,
        conflicts: list[dict[str, Any]],
    ) -> str:
        if proposed is not None:
            if conflicts:
                # Reported, never resolved silently. Scheduling over an existing commitment
                # is a failure that looks fine in the new entry and only shows up when two
                # people arrive at once.
                clashes = ", ".join(c["title"] for c in conflicts)
                return (
                    f"{proposed.title} at {proposed.start:%a %H:%M} clashes with {clashes} "
                    "— I have not added it"
                )
            return f"{proposed.title} at {proposed.start:%a %H:%M} is free; ready to add on your say-so"
        if not events:
            return "nothing in the calendar for that period"
        return f"{len(events)} entries: " + "; ".join(e["title"] for e in events[:5])


def _render(event: CalendarEvent) -> dict[str, Any]:
    return {
        "title": event.title,
        "start": event.start.isoformat(),
        "end": (event.end or event.start + event.duration).isoformat(),
        "location": event.location,
        "attendees": list(event.attendees),
    }


def _as_datetime(value: Any) -> datetime | None:
    """Parse a supplied time, or None. A naive datetime is assumed UTC and said so here —
    guessing a local zone silently is how a meeting ends up seven hours out."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
