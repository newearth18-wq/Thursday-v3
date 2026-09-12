"""Run sheets: the clock, and who cannot be in two places (V13)."""

from __future__ import annotations

from datetime import time

import pytest
from thursday_school.runsheet import RunSheetError, build

ITEMS = [
    {"name": "ลงทะเบียน", "minutes": 30, "owners": ["ครูเอ"], "location": "หน้าหอประชุม"},
    {"name": "พิธีเปิด", "minutes": 20, "owners": ["ผอ.", "ครูบี"]},
    {"name": "การแสดง ม.1", "minutes": 15, "owners": ["ครูบี"]},
    {"name": "ชมนิทรรศการ", "minutes": 60, "owners": ["ครูเอ"]},
    {"name": "พิธีปิด", "minutes": 15, "owners": ["ผอ."]},
]


def test_clock_times_are_computed_from_the_durations():
    sheet = build("เปิดบ้าน", "08:00", ITEMS)
    rows = sheet.timeline()
    assert [(r["start"], r["end"]) for r in rows] == [
        ("08:00", "08:30"),
        ("08:30", "08:50"),
        ("08:50", "09:05"),
        ("09:05", "10:05"),
        ("10:05", "10:20"),
    ]
    assert sheet.minutes == 140
    assert sheet.end == time(10, 20)


def test_an_event_that_fits_its_hard_stop_is_allowed():
    sheet = build("เปิดบ้าน", "08:00", ITEMS, must_end_by="11:00")
    assert sheet.fits and sheet.overrun == 0


def test_ending_exactly_on_the_hard_stop_fits():
    sheet = build("พอดี", "08:00", [{"name": "ก", "minutes": 60}], must_end_by="09:00")
    assert sheet.fits


def test_an_overrun_is_refused_with_both_times_and_the_gap():
    """Every item belongs to somebody who was asked to prepare it. Which one loses five
    minutes is the organiser's call."""
    with pytest.raises(RunSheetError) as raised:
        build("เกิน", "08:00", [{"name": "ก", "minutes": 200}], must_end_by="10:00")
    message = str(raised.value)
    assert "11:20" in message and "10:00" in message and "80 นาที" in message


def test_without_a_hard_stop_nothing_overruns():
    sheet = build("ไม่จำกัด", "08:00", [{"name": "ก", "minutes": 600}])
    assert sheet.fits and sheet.overrun == 0


def test_a_person_on_two_consecutive_items_is_a_clash():
    """Zero minutes to get from one to the other. Invisible in a list: the rows are far
    apart on the page and only the computed times collide."""
    clashes = build("เปิดบ้าน", "08:00", ITEMS).clashes()
    assert len(clashes) == 1
    assert clashes[0]["owner"] == "ครูบี"
    assert clashes[0]["first"] == "พิธีเปิด"
    assert clashes[0]["second"] == "การแสดง ม.1"
    assert clashes[0]["at"] == "08:50"


def test_the_same_person_with_something_in_between_is_not_a_clash():
    clashes = build(
        "เว้นช่วง",
        "08:00",
        [
            {"name": "ก", "minutes": 10, "owners": ["ครูเอ"]},
            {"name": "ข", "minutes": 10, "owners": ["ครูบี"]},
            {"name": "ค", "minutes": 10, "owners": ["ครูเอ"]},
        ],
    ).clashes()
    assert clashes == []


def test_load_is_totalled_per_person_longest_first():
    load = build("เปิดบ้าน", "08:00", ITEMS).load()
    assert load[0] == {"owner": "ครูเอ", "minutes": 90}
    assert {row["owner"] for row in load} == {"ครูเอ", "ครูบี", "ผอ."}


def test_items_nobody_is_named_on_are_reported():
    sheet = build(
        "ยังไม่ครบ",
        "08:00",
        [
            {"name": "ก", "minutes": 10, "owners": ["ครูเอ"]},
            {"name": "ข", "minutes": 10},
        ],
    )
    assert sheet.unassigned() == ["ข"]
    assert sheet.fits, "an unassigned item is a gap to report, not a reason to refuse"


def test_a_single_owner_field_is_accepted_as_well_as_a_list():
    sheet = build("คนเดียว", "08:00", [{"name": "ก", "minutes": 10, "owner": "ครูเอ"}])
    assert sheet.items[0].owners == ("ครูเอ",)


def test_an_event_that_runs_past_midnight_is_refused():
    with pytest.raises(RunSheetError, match="ข้ามวัน"):
        build("ข้ามคืน", "23:00", [{"name": "ก", "minutes": 120}])


def test_an_unreadable_time_says_what_format_to_use():
    with pytest.raises(RunSheetError, match="HH:MM"):
        build("ผิดเวลา", "8 โมง", [{"name": "ก", "minutes": 10}])


def test_a_zero_minute_item_is_refused():
    with pytest.raises(RunSheetError, match="เป็นไปไม่ได้"):
        build("ศูนย์", "08:00", [{"name": "ก", "minutes": 0}])


def test_an_empty_run_sheet_is_refused():
    with pytest.raises(RunSheetError, match="อย่างน้อยหนึ่งรายการ"):
        build("ว่าง", "08:00", [])


def test_the_document_carries_the_count_the_supervisor_checks():
    document = build("เปิดบ้าน", "08:00", ITEMS).to_dict()
    assert document["count"] == len(document["items"]) == 5
    assert document["start"] == "08:00" and document["end"] == "10:20"
