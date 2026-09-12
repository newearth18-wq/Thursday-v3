"""Rubrics: the arithmetic a parent will ask about (V13).

Asserted on exact numbers, because a rubric that is approximately right is a mark somebody
cannot defend.
"""

from __future__ import annotations

import pytest
from thursday_school.rubric import DEFAULT_BANDS, Criterion, RubricError, build


def _rubric(total=100.0, **kw):
    return build(
        "งานนำเสนอ",
        [
            {"name": "เนื้อหา", "weight": 40},
            {"name": "การนำเสนอ", "weight": 35},
            {"name": "สื่อประกอบ", "weight": 25},
        ],
        total_points=total,
        **kw,
    )


def test_points_are_the_weight_of_the_total():
    rubric = _rubric(total=20)
    assert rubric.points_for("เนื้อหา") == 8.0
    assert rubric.points_for("การนำเสนอ") == 7.0
    assert rubric.points_for("สื่อประกอบ") == 5.0
    assert sum(c.points(20) for c in rubric.criteria) == 20.0


def test_weights_that_miss_a_hundred_are_refused_not_rescaled():
    """A rubric quietly normalised is one its author can no longer recognise, and they are
    the person who has to explain it to a parent."""
    with pytest.raises(RubricError) as raised:
        build("พัง", [{"name": "ก", "weight": 40}, {"name": "ข", "weight": 40}])
    assert "80%" in str(raised.value), "the refusal must show the total that was wrong"
    assert "ก 40%" in str(raised.value), "and which weights made it"


def test_weights_a_hair_off_are_tolerated():
    """33.33 × 3 is 99.99, and that rubric is not wrong."""
    rubric = build(
        "สามเกณฑ์",
        [{"name": n, "weight": w} for n, w in (("ก", 33.33), ("ข", 33.33), ("ค", 33.34))],
    )
    assert rubric.weight_total == pytest.approx(100.0, abs=0.01)


def test_scoring_maps_bands_onto_marks():
    """ดีมาก is 4 of 4 bands, ดี is 3, พอใช้ is 2 — of each criterion's own points."""
    rubric = _rubric(total=20)
    result = rubric.score({"เนื้อหา": "ดีมาก", "การนำเสนอ": "ดี", "สื่อประกอบ": "พอใช้"})
    assert [r["marks"] for r in result["rows"]] == [8.0, 5.25, 2.5]
    assert result["earned"] == 15.75
    assert result["percent"] == 78.75
    assert result["count"] == len(result["rows"]) == 3


def test_full_marks_are_reachable_and_exact():
    rubric = _rubric(total=20)
    result = rubric.score(dict.fromkeys(("เนื้อหา", "การนำเสนอ", "สื่อประกอบ"), "ดีมาก"))
    assert result["earned"] == 20.0
    assert result["percent"] == 100.0


def test_a_criterion_left_unmarked_is_refused_rather_than_scored_zero():
    """A zero is a judgement, and nobody made it."""
    rubric = _rubric()
    with pytest.raises(RubricError, match="ยังไม่ได้ให้ระดับ"):
        rubric.score({"เนื้อหา": "ดีมาก"})


def test_an_unknown_band_names_the_ones_that_exist():
    rubric = _rubric()
    with pytest.raises(RubricError, match="ดีมาก"):
        rubric.score(dict.fromkeys(("เนื้อหา", "การนำเสนอ", "สื่อประกอบ"), "เยี่ยม"))


def test_three_bands_score_out_of_three():
    rubric = build(
        "สามระดับ",
        [{"name": "ก", "weight": 100}],
        bands=("ดี", "พอใช้", "ปรับปรุง"),
        total_points=30,
    )
    assert rubric.band_value("ดี") == 3
    assert rubric.score({"ก": "พอใช้"})["earned"] == 20.0


def test_duplicate_criteria_are_refused():
    with pytest.raises(RubricError, match="ซ้ำกัน"):
        build("ซ้ำ", [{"name": "ก", "weight": 50}, {"name": "ก", "weight": 50}])


def test_a_zero_or_negative_weight_is_refused():
    with pytest.raises(RubricError, match="มากกว่าศูนย์"):
        build("ศูนย์", [{"name": "ก", "weight": 100}, {"name": "ข", "weight": 0}])


def test_an_empty_rubric_is_refused():
    with pytest.raises(RubricError, match="อย่างน้อยหนึ่งข้อ"):
        build("ว่าง", [])


def test_descriptors_must_match_the_bands():
    with pytest.raises(RubricError, match="ระดับ"):
        build(
            "ไม่ครบ",
            [Criterion(name="ก", weight=100, descriptors=("ดีมาก", "ดี"))],
            bands=DEFAULT_BANDS,
        )


def test_a_grid_without_descriptors_is_still_a_useful_half():
    """The numbers right and the wording still to write is a legitimate state."""
    grid = _rubric().grid()
    assert grid[0]["descriptors"] == dict.fromkeys(DEFAULT_BANDS, "")


def test_the_rubric_hands_over_the_evidence_for_its_own_claim():
    """`percentages` is the key the Supervisor recomputes (§18). Shipping the weights is
    what makes "they total 100%" catchable rather than asserted."""
    document = _rubric().to_dict()
    assert document["percentages"] == [40.0, 35.0, 25.0]
    assert sum(document["percentages"]) == 100.0
    assert document["count"] == 3


def test_a_missing_criterion_name_is_refused():
    with pytest.raises(RubricError, match="ไม่มีชื่อ"):
        build("ไม่มีชื่อ", [{"name": "  ", "weight": 100}])


def test_asking_for_points_of_an_unknown_criterion_says_so():
    with pytest.raises(RubricError, match="ไม่มีเกณฑ์"):
        _rubric().points_for("ไม่มีจริง")
