"""Lesson plans whose minutes fit the period (§15, V13).

The part of a แผนการสอน that is checkable is the clock. Objectives, activities and
assessment are professional writing; *fifty minutes of activities in a fifty-minute period*
is arithmetic, and it is the thing that goes wrong — a plan that runs eight minutes over
runs over every time it is taught, and the part that gets cut is always the end, which is
where the assessment was.

So this lays the activities on a timeline, gives each one a start and a finish, and reports
what is left or what is over. It does not silently trim: a plan that does not fit is handed
back with the overrun named, because deciding what to drop is teaching rather than
arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: One คาบ in most Thai secondary schools.
DEFAULT_PERIOD_MINUTES = 50

#: The three-part shape a Thai lesson plan is expected to have. Used to say which part is
#: missing, never to refuse a plan that is organised differently.
PHASES: tuple[str, ...] = ("ขั้นนำ", "ขั้นสอน", "ขั้นสรุป")


class LessonError(ValueError):
    """A plan whose timings cannot be laid on a period."""


@dataclass(frozen=True)
class Activity:
    name: str
    minutes: int
    phase: str = ""
    #: What the pupils do, not what the teacher says. Optional.
    detail: str = ""
    materials: tuple[str, ...] = ()


@dataclass
class Lesson:
    """A single period, timed."""

    topic: str
    minutes: int = DEFAULT_PERIOD_MINUTES
    objectives: list[str] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    assessment: str = ""

    @property
    def planned(self) -> int:
        return sum(a.minutes for a in self.activities)

    @property
    def slack(self) -> int:
        """Minutes left. Negative means the plan runs over."""
        return self.minutes - self.planned

    @property
    def fits(self) -> bool:
        return self.slack >= 0

    def timeline(self) -> list[dict[str, Any]]:
        """Each activity with the clock time it starts and ends, in minutes from the bell."""
        out: list[dict[str, Any]] = []
        clock = 0
        for activity in self.activities:
            out.append(
                {
                    "name": activity.name,
                    "phase": activity.phase,
                    "start": clock,
                    "end": clock + activity.minutes,
                    "minutes": activity.minutes,
                    "detail": activity.detail,
                    "materials": list(activity.materials),
                }
            )
            clock += activity.minutes
        return out

    def missing_phases(self) -> list[str]:
        """Which of the three parts nothing covers. Reported, never enforced."""
        present = {a.phase for a in self.activities if a.phase}
        return [p for p in PHASES if p not in present]

    def materials(self) -> list[str]:
        """Everything the period needs, deduplicated, in the order it is first wanted."""
        seen: list[str] = []
        for activity in self.activities:
            for item in activity.materials:
                if item and item not in seen:
                    seen.append(item)
        return seen

    def to_dict(self) -> dict[str, Any]:
        timeline = self.timeline()
        return {
            "topic": self.topic,
            "minutes": self.minutes,
            "objectives": list(self.objectives),
            "items": timeline,
            "count": len(timeline),
            "planned_minutes": self.planned,
            "slack_minutes": self.slack,
            "fits": self.fits,
            "missing_phases": self.missing_phases(),
            "materials": self.materials(),
            "assessment": self.assessment,
        }


def build(
    topic: str,
    activities: list[Activity] | list[dict[str, Any]],
    *,
    minutes: int = DEFAULT_PERIOD_MINUTES,
    objectives: list[str] | None = None,
    assessment: str = "",
) -> Lesson:
    """Build a plan, refusing one that cannot be taught in the time it claims."""
    if not topic.strip():
        raise LessonError("แผนการสอนต้องมีชื่อเรื่อง")
    if minutes <= 0:
        raise LessonError(f"คาบเรียน {minutes} นาที เป็นไปไม่ได้")
    if not activities:
        raise LessonError("แผนการสอนต้องมีกิจกรรมอย่างน้อยหนึ่งกิจกรรม")

    parsed = [a if isinstance(a, Activity) else _activity(a) for a in activities]

    lesson = Lesson(
        topic=topic.strip(),
        minutes=minutes,
        objectives=list(objectives or []),
        activities=parsed,
        assessment=assessment,
    )

    if not lesson.fits:
        # Named, not trimmed. Which activity to shorten is a teaching decision, and a plan
        # quietly cut to fit is one whose author discovers the cut while teaching it.
        raise LessonError(
            f"กิจกรรมรวม {lesson.planned} นาที เกินคาบ {minutes} นาที อยู่ "
            f"{-lesson.slack} นาที — " + ", ".join(f"{a.name} {a.minutes} นาที" for a in parsed)
        )
    return lesson


def _activity(raw: dict[str, Any]) -> Activity:
    try:
        name = str(raw["name"]).strip()
        minutes = int(raw["minutes"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LessonError(f"กิจกรรมไม่ครบ (ต้องมี name และ minutes): {raw!r}") from exc
    if not name:
        raise LessonError("มีกิจกรรมที่ไม่มีชื่อ")
    if minutes <= 0:
        raise LessonError(f"กิจกรรม {name!r} ใช้เวลา {minutes} นาที เป็นไปไม่ได้")

    phase = str(raw.get("phase", "")).strip()
    if phase and phase not in PHASES:
        raise LessonError(f"ขั้นตอน {phase!r} ไม่รู้จัก — ใช้ได้: " + ", ".join(PHASES))

    return Activity(
        name=name,
        minutes=minutes,
        phase=phase,
        detail=str(raw.get("detail", "")),
        materials=tuple(str(m) for m in raw.get("materials") or ()),
    )
