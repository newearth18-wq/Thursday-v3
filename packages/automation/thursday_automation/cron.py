"""Five-field cron, evaluated in the owner's timezone (V15).

`Trigger(kind="schedule", cron=...)` has existed since the automation engine was written
and **nothing has ever fired one.** `Automation.should_fire` only answers for event
triggers, and no loop anywhere looked at the clock. A rule saying *every weekday at 07:30*
was stored, listed, and silently never ran.

That is the failure the workflow builder would have shipped at ten times the scale: an
owner drags a schedule node onto a canvas, is shown a picture of a thing that runs every
morning, and gets nothing. So the matcher comes first and the canvas comes after.

**No dependency.** `croniter` would do this, but a cron field is a small grammar and the
behaviour that matters is the one libraries differ on — see `_day_matches` — so it is
written out and tested rather than imported and assumed.

**An expression that cannot be parsed is refused**, not narrowed to `*`. A rule the owner
believes runs at 07:30 and which actually runs every minute is worse than one that would
not save.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CronRefused(ValueError):
    """An expression this module will not pretend to understand."""


#: minute, hour, day-of-month, month, day-of-week — the POSIX order and the one every
#: crontab on the owner's machine uses. Ranges are inclusive at both ends.
FIELDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day", 1, 31),
    ("month", 1, 12),
    ("weekday", 0, 6),
)

#: Names accepted in place of numbers, because "MON" is what a person writes and a rule
#: that only accepts 1 is a rule people get wrong at the boundary (is Monday 0 or 1?).
_NAMES: dict[str, int] = {
    "SUN": 0,
    "MON": 1,
    "TUE": 2,
    "WED": 3,
    "THU": 4,
    "FRI": 5,
    "SAT": 6,
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


@dataclass(frozen=True)
class Schedule:
    """A parsed expression. Frozen: the sets are the parse, and re-deriving them per tick
    would be the same work sixty times a minute."""

    expression: str
    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    weekday: frozenset[int]
    #: Whether each field was written as something other than `*`. `_day_matches` needs
    #: to know for day and weekday, and "is this set smaller than the full range" is not
    #: the same question once somebody writes `1-31`.
    day_restricted: bool = False
    weekday_restricted: bool = False
    hour_restricted: bool = False

    def matches(self, when: datetime) -> bool:
        """Does this minute fall on the schedule? Seconds are ignored, never rounded."""
        return (
            when.minute in self.minute
            and when.hour in self.hour
            and when.month in self.month
            and self._day_matches(when)
        )

    def _day_matches(self, when: datetime) -> bool:
        """The POSIX rule, which surprises everyone who has not been bitten by it.

        When **both** day-of-month and day-of-week are restricted, cron fires when
        **either** matches — not both. `0 0 1 * MON` is "the first of the month, and also
        every Monday", not "Mondays that fall on the first".

        Getting this backwards is not a small error. A rule the owner reads as "the 1st or
        Mondays" would fire perhaps once a year, and they would find out by it not having
        happened. So it is implemented, named, and tested both ways round.
        """
        # Python: Monday is 0. Cron: Sunday is 0.
        weekday = (when.weekday() + 1) % 7
        by_day = when.day in self.day
        by_weekday = weekday in self.weekday
        if self.day_restricted and self.weekday_restricted:
            return by_day or by_weekday
        return by_day and by_weekday


def parse(expression: str) -> Schedule:
    """Parse a five-field expression, or refuse it with the field that was wrong."""
    text = (expression or "").strip()
    if not text:
        raise CronRefused("ตารางเวลาว่างเปล่า")

    parts = text.split()
    if len(parts) != 5:
        raise CronRefused(f"cron ต้องมี 5 ช่อง (นาที ชั่วโมง วันที่ เดือน วัน) — ได้ {len(parts)}: {text!r}")

    values: dict[str, frozenset[int]] = {}
    restricted: dict[str, bool] = {}
    for part, (name, low, high) in zip(parts, FIELDS, strict=True):
        values[name] = _field(part, name, low, high)
        restricted[name] = part.strip() != "*"

    return Schedule(
        expression=text,
        minute=values["minute"],
        hour=values["hour"],
        day=values["day"],
        month=values["month"],
        weekday=values["weekday"],
        day_restricted=restricted["day"],
        weekday_restricted=restricted["weekday"],
        hour_restricted=restricted["hour"],
    )


def _field(raw: str, name: str, low: int, high: int) -> frozenset[int]:
    out: set[int] = set()
    for piece in raw.split(","):
        out |= _piece(piece.strip(), name, low, high)
    if not out:
        raise CronRefused(f"ช่อง {name} ว่างเปล่า: {raw!r}")
    return frozenset(out)


def _piece(piece: str, name: str, low: int, high: int) -> set[int]:
    if not piece:
        raise CronRefused(f"ช่อง {name} มีรายการว่าง")

    step = 1
    if "/" in piece:
        piece, _, step_text = piece.partition("/")
        try:
            step = int(step_text)
        except ValueError:
            raise CronRefused(f"ช่อง {name}: ก้าว {step_text!r} ไม่ใช่ตัวเลข") from None
        if step < 1:
            raise CronRefused(f"ช่อง {name}: ก้าวต้องมากกว่า 0 — ได้ {step}")

    if piece in ("*", ""):
        start, stop = low, high
    elif "-" in piece.lstrip("-"):
        start_text, _, stop_text = piece.partition("-")
        start, stop = _number(start_text, name, low, high), _number(stop_text, name, low, high)
        if start > stop:
            # Refused rather than wrapped. `FRI-MON` could mean the weekend or could be a
            # typo for `MON-FRI`, and guessing which would silently change when a rule runs.
            raise CronRefused(f"ช่อง {name}: ช่วง {piece!r} เริ่มหลังจบ")
    else:
        start = stop = _number(piece, name, low, high)

    return set(range(start, stop + 1, step))


def _number(text: str, name: str, low: int, high: int) -> int:
    token = text.strip().upper()
    if token in _NAMES and name in ("weekday", "month"):
        value = _NAMES[token]
    else:
        try:
            value = int(token)
        except ValueError:
            raise CronRefused(f"ช่อง {name}: {text!r} ไม่ใช่ตัวเลขหรือชื่อที่รู้จัก") from None

    # Sunday is written 0 or 7; both are Sunday, and a rule written `7` must not be silently
    # out of range.
    if name == "weekday" and value == 7:
        value = 0

    if not low <= value <= high:
        raise CronRefused(f"ช่อง {name}: {value} อยู่นอกช่วง {low}–{high}")
    return value


def zone(name: str) -> ZoneInfo:
    """The owner's timezone, or a refusal naming it.

    Falling back to UTC would move every schedule by seven hours here and say nothing.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronRefused(f"ไม่รู้จักเขตเวลา {name!r}: {exc}") from None


def describe(expression: str, *, locale: str = "th") -> str:
    """A plain-language reading of the expression, for the owner to check against intent.

    Deliberately conservative: it describes the common shapes exactly and otherwise says
    what the fields are rather than inventing a sentence. A confident wrong sentence about
    when a rule runs is the thing this whole module exists to prevent.
    """
    schedule = parse(expression)
    minute, hour = sorted(schedule.minute), sorted(schedule.hour)

    if len(minute) == 1 and len(hour) == 1:
        clock = f"{hour[0]:02d}:{minute[0]:02d}"
    elif len(minute) == 1 and not schedule.hour_restricted:
        # "every hour" already says every day; appending ทุกวัน reads as a second claim.
        clock = f"ทุกชั่วโมง นาทีที่ {minute[0]}"
        if not schedule.day_restricted and not schedule.weekday_restricted:
            return clock
    else:
        return f"ตามรูปแบบ {schedule.expression}"

    return f"{clock} {_days(schedule)}".strip()


def _days(schedule: Schedule) -> str:
    thai = ("อาทิตย์", "จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์")
    if not schedule.day_restricted and not schedule.weekday_restricted:
        return "ทุกวัน"
    if schedule.weekday_restricted and not schedule.day_restricted:
        days = sorted(schedule.weekday)
        if days == [1, 2, 3, 4, 5]:
            return "วันจันทร์ถึงศุกร์"
        if days == [0, 6]:
            return "วันเสาร์อาทิตย์"
        return "วัน" + " ".join(thai[d] for d in days)
    if schedule.day_restricted and not schedule.weekday_restricted:
        return "วันที่ " + ", ".join(str(d) for d in sorted(schedule.day))
    # Both restricted: the OR rule above. Said out loud, because a reader who does not know
    # it will read the sentence as "and".
    return (
        "วันที่ "
        + ", ".join(str(d) for d in sorted(schedule.day))
        + " หรือวัน"
        + " ".join(thai[d] for d in sorted(schedule.weekday))
    )
