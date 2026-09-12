"""Lesson plans: the clock (V13)."""

from __future__ import annotations

import pytest
from thursday_school.lesson import PHASES, LessonError, build

ACTIVITIES = [
    {"name": "ทบทวนแรง", "minutes": 5, "phase": "ขั้นนำ"},
    {"name": "ทดลองลากวัตถุ", "minutes": 25, "phase": "ขั้นสอน", "materials": ["กล่องทดลอง", "สปริง"]},
    {"name": "อภิปรายผล", "minutes": 12, "phase": "ขั้นสอน", "materials": ["กระดาน"]},
    {"name": "สรุปและใบงาน", "minutes": 6, "phase": "ขั้นสรุป", "materials": ["ใบงาน"]},
]


def test_activities_are_laid_on_a_clock_from_the_bell():
    lesson = build("แรงเสียดทาน", ACTIVITIES)
    timeline = lesson.timeline()
    assert [(t["start"], t["end"]) for t in timeline] == [(0, 5), (5, 30), (30, 42), (42, 48)]
    assert lesson.planned == 48
    assert lesson.slack == 2
    assert lesson.fits


def test_a_plan_that_overruns_is_refused_with_the_overrun_named():
    """Which activity to shorten is teaching. A plan quietly cut to fit is one whose author
    discovers the cut while teaching it."""
    with pytest.raises(LessonError) as raised:
        build("เกิน", [{"name": "ก", "minutes": 30}, {"name": "ข", "minutes": 30}])
    message = str(raised.value)
    assert "60" in message and "50" in message and "10" in message
    assert "ก 30 นาที" in message


def test_a_plan_that_exactly_fills_the_period_is_allowed():
    lesson = build("พอดี", [{"name": "ก", "minutes": 50}])
    assert lesson.slack == 0
    assert lesson.fits


def test_a_shorter_period_is_honoured():
    with pytest.raises(LessonError, match="เกินคาบ 30 นาที"):
        build("คาบสั้น", [{"name": "ก", "minutes": 40}], minutes=30)


def test_materials_are_gathered_in_the_order_they_are_first_wanted():
    assert build("แรงเสียดทาน", ACTIVITIES).materials() == ["กล่องทดลอง", "สปริง", "กระดาน", "ใบงาน"]


def test_a_material_used_twice_is_listed_once():
    lesson = build(
        "ซ้ำ",
        [
            {"name": "ก", "minutes": 10, "materials": ["ใบงาน"]},
            {"name": "ข", "minutes": 10, "materials": ["ใบงาน", "กระดาน"]},
        ],
    )
    assert lesson.materials() == ["ใบงาน", "กระดาน"]


def test_a_missing_phase_is_reported_never_enforced():
    """A plan organised differently is not a plan to refuse."""
    lesson = build("ไม่มีสรุป", [{"name": "ก", "minutes": 10, "phase": "ขั้นนำ"}])
    assert lesson.missing_phases() == ["ขั้นสอน", "ขั้นสรุป"]
    assert lesson.fits, "reporting a gap must not fail the plan"


def test_a_plan_with_all_three_phases_reports_none_missing():
    assert build("ครบ", ACTIVITIES).missing_phases() == []
    assert set(PHASES) == {"ขั้นนำ", "ขั้นสอน", "ขั้นสรุป"}


def test_an_unknown_phase_is_refused():
    with pytest.raises(LessonError, match="ไม่รู้จัก"):
        build("ผิดขั้น", [{"name": "ก", "minutes": 10, "phase": "ขั้นทดสอบ"}])


def test_an_activity_with_no_time_is_refused():
    with pytest.raises(LessonError, match="เป็นไปไม่ได้"):
        build("ศูนย์นาที", [{"name": "ก", "minutes": 0}])


def test_a_plan_with_no_activities_is_refused():
    with pytest.raises(LessonError, match="อย่างน้อยหนึ่งกิจกรรม"):
        build("ว่าง", [])


def test_a_plan_with_no_topic_is_refused():
    with pytest.raises(LessonError, match="ชื่อเรื่อง"):
        build("   ", ACTIVITIES)


def test_the_document_carries_the_count_the_supervisor_checks():
    document = build("แรงเสียดทาน", ACTIVITIES).to_dict()
    assert document["count"] == len(document["items"]) == 4
    assert document["planned_minutes"] == 48
    assert document["slack_minutes"] == 2
