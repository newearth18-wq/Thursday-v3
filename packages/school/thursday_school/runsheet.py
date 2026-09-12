"""Run sheets: a timeline that closes, and the clashes in it (§15, V13).

An event run sheet is the document everybody works from on the day — MC, sound, stage,
teachers herding classes — and its failure mode is arithmetic. Items are written with
durations, somebody writes clock times beside them by hand, and the two stop agreeing
somewhere in the middle of the afternoon. By the time anyone notices, the closing ceremony
is at 15:40 and the buses are at 15:30.

So the clock times are computed from the durations, never written alongside them. Give it a
start time and a list of items, and it lays them end to end and tells you when the thing
actually finishes. A run sheet that must end at a fixed time is checked against it and the
overrun is named — not trimmed, because which item loses five minutes is the organiser's
call and every one of them belongs to somebody who was asked to prepare it.

The second thing it does is **who is doing what, when**. A person assigned to two items that
overlap is the defect a run sheet exists to prevent, and it is invisible in a list: the two
rows are far apart on the page and their times only collide once the clock is worked out.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from typing import Any


class RunSheetError(ValueError):
    """A run sheet that cannot be run."""


@dataclass(frozen=True)
class Item:
    """One thing that happens, and how long it takes."""

    name: str
    minutes: int
    #: Whoever is on for it — MC, a teacher, a class. Several is normal.
    owners: tuple[str, ...] = ()
    location: str = ""
    notes: str = ""


@dataclass
class RunSheet:
    """An event, laid on a clock."""

    title: str
    start: time
    items: list[Item] = field(default_factory=list)
    #: The hard stop, when there is one. Buses, a booked hall, the next lesson.
    must_end_by: time | None = None

    @property
    def minutes(self) -> int:
        return sum(i.minutes for i in self.items)

    def _at(self, offset: int) -> time:
        base = datetime.combine(date(2000, 1, 1), self.start)
        return (base + timedelta(minutes=offset)).time()

    @property
    def end(self) -> time:
        return self._at(self.minutes)

    @property
    def overrun(self) -> int:
        """Minutes past the hard stop. Zero when there is none or it fits."""
        if self.must_end_by is None:
            return 0
        planned = _minutes(self.end)
        limit = _minutes(self.must_end_by)
        return max(0, planned - limit)

    @property
    def fits(self) -> bool:
        return self.overrun == 0

    def timeline(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        for item in self.items:
            rows.append(
                {
                    "name": item.name,
                    "start": self._at(offset).strftime("%H:%M"),
                    "end": self._at(offset + item.minutes).strftime("%H:%M"),
                    "minutes": item.minutes,
                    "owners": list(item.owners),
                    "location": item.location,
                    "notes": item.notes,
                }
            )
            offset += item.minutes
        return rows

    def clashes(self) -> list[dict[str, Any]]:
        """Anyone on for two items at once.

        Items run back to back here, so a clash means the same person is named on two
        *consecutive* items with no gap — they are expected to finish one and be in place
        for the next with zero minutes to move. Reported as a clash because on the day it
        is one.
        """
        rows = self.timeline()
        by_owner: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            for owner in row["owners"]:
                by_owner[owner].append(row)

        found: list[dict[str, Any]] = []
        for owner, owned in sorted(by_owner.items()):
            for first, second in pairwise(owned):
                if first["end"] == second["start"]:
                    found.append(
                        {
                            "owner": owner,
                            "first": first["name"],
                            "second": second["name"],
                            "at": first["end"],
                        }
                    )
        return found

    def load(self) -> list[dict[str, Any]]:
        """How many minutes each person is on for."""
        totals: defaultdict[str, int] = defaultdict(int)
        for item in self.items:
            for owner in item.owners:
                totals[owner] += item.minutes
        return [
            {"owner": o, "minutes": m}
            for o, m in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def unassigned(self) -> list[str]:
        """Items nobody is named on. Reported, never invented."""
        return [i.name for i in self.items if not i.owners]

    def to_dict(self) -> dict[str, Any]:
        rows = self.timeline()
        clashes = self.clashes()
        return {
            "title": self.title,
            "start": self.start.strftime("%H:%M"),
            "end": self.end.strftime("%H:%M"),
            "minutes": self.minutes,
            "items": rows,
            "count": len(rows),
            "must_end_by": self.must_end_by.strftime("%H:%M") if self.must_end_by else None,
            "overrun_minutes": self.overrun,
            "fits": self.fits,
            "clashes": clashes,
            "load": self.load(),
            "unassigned": self.unassigned(),
        }


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def build(
    title: str,
    start: str | time,
    items: list[Item] | list[dict[str, Any]],
    *,
    must_end_by: str | time | None = None,
) -> RunSheet:
    """Lay an event on the clock, refusing one that cannot be run as written."""
    if not title.strip():
        raise RunSheetError("กำหนดการต้องมีชื่องาน")
    if not items:
        raise RunSheetError("กำหนดการต้องมีรายการอย่างน้อยหนึ่งรายการ")

    parsed = [i if isinstance(i, Item) else _item(i) for i in items]
    sheet = RunSheet(
        title=title.strip(),
        start=_time(start),
        items=parsed,
        must_end_by=_time(must_end_by) if must_end_by else None,
    )

    if _minutes(sheet.start) + sheet.minutes >= 24 * 60:
        raise RunSheetError(f"กำหนดการยาว {sheet.minutes} นาที เริ่ม {sheet.start:%H:%M} แล้วข้ามวัน")

    if not sheet.fits:
        # Named rather than trimmed. Every item belongs to somebody who was asked to
        # prepare it, and which one loses five minutes is the organiser's call.
        raise RunSheetError(
            f"กำหนดการจบ {sheet.end:%H:%M} เกินเวลาที่ต้องจบ "
            f"{sheet.must_end_by:%H:%M} อยู่ {sheet.overrun} นาที"
        )
    return sheet


def _item(raw: dict[str, Any]) -> Item:
    try:
        name = str(raw["name"]).strip()
        minutes = int(raw["minutes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RunSheetError(f"รายการไม่ครบ (ต้องมี name และ minutes): {raw!r}") from exc
    if not name:
        raise RunSheetError("มีรายการที่ไม่มีชื่อ")
    if minutes <= 0:
        raise RunSheetError(f"รายการ {name!r} ใช้เวลา {minutes} นาที เป็นไปไม่ได้")

    owners = raw.get("owners") or ([raw["owner"]] if raw.get("owner") else [])
    return Item(
        name=name,
        minutes=minutes,
        owners=tuple(str(o).strip() for o in owners if str(o).strip()),
        location=str(raw.get("location", "")),
        notes=str(raw.get("notes", "")),
    )


def _time(value: str | time) -> time:
    if isinstance(value, time):
        return value
    try:
        return time.fromisoformat(str(value))
    except ValueError as exc:
        raise RunSheetError(f"เวลา {value!r} อ่านไม่ได้ — ใช้รูปแบบ HH:MM") from exc
