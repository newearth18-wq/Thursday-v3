"""Exam blueprints: cells against declared totals (V13)."""

from __future__ import annotations

import pytest
from thursday_school.blueprint import LEVELS, BlueprintError, build

ROWS = [
    {
        "topic": "สารและสมบัติ",
        "items": {"จำ": 4, "เข้าใจ": 3, "ประยุกต์ใช้": 2},
        "marks_each": 1,
        "periods": 6,
    },
    {
        "topic": "แรงและการเคลื่อนที่",
        "items": {"เข้าใจ": 3, "วิเคราะห์": 2},
        "marks_each": 2,
        "periods": 8,
    },
    {"topic": "พลังงาน", "items": {"จำ": 2, "ประเมินค่า": 1}, "marks_each": 3, "periods": 4},
]


def test_items_and_marks_are_counted_from_the_cells():
    blueprint = build("กลางภาค", ROWS)
    assert blueprint.total_items == 17
    assert blueprint.total_marks == 28.0
    assert blueprint.by_level()["จำ"] == 6
    assert blueprint.by_level()["สร้างสรรค์"] == 0


def test_a_declared_item_count_that_disagrees_is_refused_with_both_numbers():
    with pytest.raises(BlueprintError) as raised:
        build("ผิด", ROWS, expect_items=20)
    assert "20" in str(raised.value) and "17" in str(raised.value)


def test_a_declared_mark_total_that_disagrees_is_refused():
    with pytest.raises(BlueprintError, match="30"):
        build("ผิด", ROWS, expect_marks=30)


def test_the_declared_totals_pass_when_they_match():
    assert build("ถูก", ROWS, expect_items=17, expect_marks=28).total_items == 17


def test_higher_order_share_is_of_marks_not_items():
    """Five one-mark recall questions and one ten-mark analysis is not five-sixths recall."""
    blueprint = build(
        "ถ่วงน้ำหนัก",
        [
            {"topic": "ก", "items": {"จำ": 5}, "marks_each": 1},
            {"topic": "ข", "items": {"วิเคราะห์": 1}, "marks_each": 10},
        ],
    )
    assert blueprint.total_items == 6
    assert blueprint.higher_order_share() == pytest.approx(66.67, abs=0.01)


def test_shares_add_to_a_hundred_and_are_handed_to_the_supervisor():
    document = build("กลางภาค", ROWS).to_dict()
    assert sum(document["percentages"]) == pytest.approx(100.0, abs=0.05)
    assert document["count"] == 3


def test_the_gap_between_marks_and_teaching_time_is_reported_never_judged():
    """A twelve-point gap may be exactly right. Stating it is the help; ruling on it would
    be inventing a standard."""
    shares = build("กลางภาค", ROWS).shares()
    energy = next(s for s in shares if s["topic"] == "พลังงาน")
    assert energy["taught_share"] == pytest.approx(22.22, abs=0.01)
    assert energy["gap"] == pytest.approx(9.92, abs=0.01)


def test_without_stated_periods_no_gap_is_invented():
    shares = build("ไม่ระบุคาบ", [{"topic": "ก", "items": {"จำ": 2}}]).shares()
    assert shares[0]["taught_share"] is None
    assert shares[0]["gap"] is None


def test_a_topic_with_no_items_is_named_rather_than_dropped():
    """Either an omission or a decision. Either way the teacher should see it named rather
    than find a paper that skips a unit they taught."""
    with pytest.raises(BlueprintError, match="ยังไม่มีข้อสอบ"):
        build("ขาด", [{"topic": "ก", "items": {"จำ": 2}}, {"topic": "ข", "items": {}}])


def test_an_unknown_cognitive_level_lists_the_real_ones():
    with pytest.raises(BlueprintError) as raised:
        build("ผิดระดับ", [{"topic": "ก", "items": {"ท่องจำ": 2}}])
    assert "ท่องจำ" in str(raised.value)
    assert "วิเคราะห์" in str(raised.value)


def test_duplicate_topics_are_refused():
    with pytest.raises(BlueprintError, match="ซ้ำกัน"):
        build("ซ้ำ", [{"topic": "ก", "items": {"จำ": 1}}, {"topic": "ก", "items": {"จำ": 1}}])


def test_zero_marks_per_item_is_refused():
    with pytest.raises(BlueprintError, match="มากกว่าศูนย์"):
        build("ศูนย์", [{"topic": "ก", "items": {"จำ": 1}, "marks_each": 0}])


def test_an_empty_blueprint_is_refused():
    with pytest.raises(BlueprintError, match="อย่างน้อยหนึ่งหน่วย"):
        build("ว่าง", [])


def test_every_level_appears_in_the_row_even_when_unused():
    """A printed blueprint has a column per level; a missing key is a missing column."""
    row = build("เต็มคอลัมน์", [{"topic": "ก", "items": {"จำ": 1}}]).to_dict()["rows"][0]
    assert set(row["items"]) == set(LEVELS)
    assert row["items"]["สร้างสรรค์"] == 0
