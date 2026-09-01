"""Routine learning (§49).

Mines the tool-run history for sequences the owner repeats, and *proposes* an automation.
Thursday never creates one silently — the brief is explicit about that, and a system that
quietly automates things is a system nobody trusts with a filesystem.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from thursday.automation.rules import Action, Automation, Trigger
from thursday.core.logging import get_logger
from thursday.shared.enums import ProactivityLevel

log = get_logger(__name__)

#: A pattern must recur this many times, across this many distinct days, inside a
#: two-hour daily band, before it is worth mentioning.
MIN_OCCURRENCES = 4
MIN_DISTINCT_DAYS = 3
BAND_HOURS = 2


@dataclass(frozen=True)
class RoutineCandidate:
    tools: tuple[str, ...]
    hour_band: int
    occurrences: int
    distinct_days: int

    def describe(self, language: str = "th") -> str:
        sequence = ", ".join(self.tools)
        window = f"{self.hour_band:02d}:00–{self.hour_band + BAND_HOURS:02d}:00"
        if language == "th":
            return (
                f"ช่วง {window} คุณทำ {sequence} ซ้ำ {self.occurrences} ครั้ง "
                f"ใน {self.distinct_days} วัน — ต้องการให้ผมสร้าง Routine ไหม"
            )
        return (
            f"Between {window} you run {sequence} — {self.occurrences} times across "
            f"{self.distinct_days} days. Shall I make that a routine?"
        )

    def to_automation(self) -> Automation:
        """Build the proposal. It arrives **disabled**; the owner enables it."""
        return Automation(
            name=f"routine-{self.hour_band:02d}00-{'-'.join(self.tools)}"[:60],
            trigger=Trigger(kind="schedule", cron=f"0 {self.hour_band} * * *"),
            actions=[Action(kind="tool", name=tool) for tool in self.tools],
            enabled=False,
            created_by="thursday_suggested",
            proactivity_min=ProactivityLevel.NORMAL,
        )


class RoutineLearner:
    """Consumes ``tool.executed`` events and reports candidates on request."""

    def __init__(self) -> None:
        self._runs: list[tuple[datetime, str]] = []
        self._proposed: set[tuple[str, ...]] = set()

    def attach(self, bus: object) -> None:
        bus.subscribe("tool.executed", self.on_tool)  # type: ignore[attr-defined]

    async def on_tool(self, event) -> None:
        tool = str(event.payload.get("tool", ""))
        if tool:
            self._runs.append((event.occurred_at, tool))

    def candidates(self) -> list[RoutineCandidate]:
        """Group runs into daily bands and look for repeated tool sets."""
        by_band: dict[tuple[int, str], set[str]] = defaultdict(set)
        counts: dict[tuple[int, frozenset[str]], int] = defaultdict(int)

        for when, tool in self._runs:
            band = (when.hour // BAND_HOURS) * BAND_HOURS
            by_band[(band, when.date().isoformat())].add(tool)

        for (band, _day), tools in by_band.items():
            if len(tools) >= 2:
                counts[(band, frozenset(tools))] += 1

        out: list[RoutineCandidate] = []
        for (band, tools), days in counts.items():
            occurrences = sum(
                1
                for when, tool in self._runs
                if (when.hour // BAND_HOURS) * BAND_HOURS == band and tool in tools
            )
            if occurrences >= MIN_OCCURRENCES and days >= MIN_DISTINCT_DAYS:
                out.append(
                    RoutineCandidate(
                        tools=tuple(sorted(tools)),
                        hour_band=band,
                        occurrences=occurrences,
                        distinct_days=days,
                    )
                )
        out.sort(key=lambda c: c.occurrences, reverse=True)
        return out

    def unproposed(self) -> list[RoutineCandidate]:
        """Candidates the owner has not already been asked about."""
        return [c for c in self.candidates() if c.tools not in self._proposed]

    def mark_proposed(self, candidate: RoutineCandidate) -> None:
        self._proposed.add(candidate.tools)
