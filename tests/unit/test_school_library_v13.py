"""Circulation and collection: counting, and refusing to guess (V13)."""

from __future__ import annotations

from datetime import date

import pytest
from thursday_school.circulation import CirculationError, summarise
from thursday_school.circulation import build as build_loans
from thursday_school.collection import CollectionError, analyse, gaps
from thursday_school.collection import build as build_items

ASOF = date(2026, 7, 1)

LOANS = [
    # returned in time
    {
        "item": "ดาราศาสตร์",
        "borrower": "ก",
        "group": "ม.1/1",
        "category": "วิทย์",
        "borrowed_on": "2026-06-02",
        "due_on": "2026-06-16",
        "returned_on": "2026-06-14",
    },
    # still out, past due
    {
        "item": "ดาราศาสตร์",
        "borrower": "ข",
        "group": "ม.1/2",
        "category": "วิทย์",
        "borrowed_on": "2026-05-10",
        "due_on": "2026-05-24",
    },
    # still out, not yet due
    {
        "item": "นิทานไทย",
        "borrower": "ค",
        "group": "ม.1/1",
        "category": "วรรณกรรม",
        "borrowed_on": "2026-06-25",
        "due_on": "2026-07-09",
    },
    # returned, but late
    {
        "item": "ประวัติศาสตร์",
        "borrower": "ก",
        "group": "ม.1/1",
        "category": "สังคม",
        "borrowed_on": "2026-06-01",
        "due_on": "2026-06-15",
        "returned_on": "2026-06-21",
    },
]


# ------------------------------------------------------------------------- circulation


def test_the_four_states_of_a_loan_are_counted_separately():
    report = summarise(build_loans(LOANS), asof=ASOF)
    assert report["count"] == 4
    assert report["still_out"] == 2, "one past due, one not yet due"
    assert report["overdue"] == 1, "only the one still out and past its date"
    assert report["returned_late"] == 1, "the one that came back late is not 'overdue'"
    assert report["on_time_share"] == 50.0


def test_overdue_is_answered_as_at_a_date_not_as_at_now():
    """A report run in October about September must say what was overdue in September."""
    loans = build_loans(LOANS)
    assert summarise(loans, asof=date(2026, 5, 20))["overdue"] == 0
    assert summarise(loans, asof=date(2026, 7, 30))["overdue"] == 2
    assert summarise(loans, asof=ASOF)["asof"] == "2026-07-01"


def test_days_overdue_counts_to_the_return_not_to_today():
    loan = build_loans([LOANS[3]])[0]
    assert loan.days_overdue(date(2027, 1, 1)) == 6, "returned 6 days late, and stays 6"


def test_group_shares_are_complete_and_offered_for_checking():
    report = summarise(build_loans(LOANS), asof=ASOF)
    assert sum(report["percentages"]) == pytest.approx(100.0, abs=0.05)
    assert report["by_group"][0] == {"group": "ม.1/1", "loans": 3, "share": 75.0}


def test_per_pupil_needs_the_roll_and_is_omitted_without_it():
    """Dividing by the children who happened to borrow flatters a library exactly in
    proportion to how few used it."""
    loans = build_loans(LOANS)
    assert "loans_per_pupil" not in summarise(loans, asof=ASOF)

    with_roll = summarise(loans, asof=ASOF, enrolled=200)
    assert with_roll["loans_per_pupil"] == 0.02
    assert with_roll["reach_percent"] == 1.5, "3 borrowers of 200"


def test_an_impossible_roll_is_refused():
    with pytest.raises(CirculationError, match="เป็นไปไม่ได้"):
        summarise(build_loans(LOANS), asof=ASOF, enrolled=0)


def test_the_most_overdue_are_ranked_longest_first():
    report = summarise(build_loans(LOANS), asof=ASOF, top=3)
    assert [m["item"] for m in report["most_overdue"]] == ["ดาราศาสตร์"]
    assert report["most_overdue"][0]["days"] == 38


def test_titles_are_counted_apart_from_loans():
    report = summarise(build_loans(LOANS), asof=ASOF)
    assert report["count"] == 4 and report["titles"] == 3, "one title went out twice"
    assert report["top_titles"][0] == {"item": "ดาราศาสตร์", "loans": 2}


def test_months_are_ordered_chronologically():
    months = [m["month"] for m in summarise(build_loans(LOANS), asof=ASOF)["by_month"]]
    assert months == sorted(months) == ["2026-05", "2026-06"]


def test_a_due_date_before_the_loan_date_is_refused():
    with pytest.raises(CirculationError, match="ก่อนวันยืม"):
        build_loans(
            [{"item": "ก", "borrower": "1", "borrowed_on": "2026-06-10", "due_on": "2026-06-01"}]
        )


def test_a_return_before_the_loan_is_refused():
    with pytest.raises(CirculationError, match="ก่อนวันยืม"):
        build_loans(
            [
                {
                    "item": "ก",
                    "borrower": "1",
                    "borrowed_on": "2026-06-10",
                    "due_on": "2026-06-24",
                    "returned_on": "2026-06-01",
                }
            ]
        )


def test_an_unreadable_date_says_what_format_to_use():
    with pytest.raises(CirculationError, match="YYYY-MM-DD"):
        build_loans(
            [{"item": "ก", "borrower": "1", "borrowed_on": "10/06/2026", "due_on": "2026-06-24"}]
        )


def test_summarising_nothing_is_refused():
    with pytest.raises(CirculationError, match="ไม่มีรายการ"):
        build_loans([])


# -------------------------------------------------------------------------- collection

ITEMS = [
    {"title": "ดาราศาสตร์", "category": "วิทยาศาสตร์", "year": 2545, "copies": 4},
    {"title": "ฟิสิกส์ใหม่", "category": "วิทยาศาสตร์", "year": 2566, "copies": 2},
    {"title": "นิทานไทย", "category": "วรรณกรรม", "year": 2560, "copies": 6},
    {"title": "แผนที่", "category": "สังคม", "copies": 3},  # year unknown
]


def test_copies_are_counted_not_titles():
    report = analyse(build_items(ITEMS))
    assert report["titles"] == 4
    assert report["copies"] == 15


def test_category_shares_are_of_copies_and_total_a_hundred():
    report = analyse(build_items(ITEMS))
    assert sum(report["percentages"]) == pytest.approx(100.0, abs=0.05)
    assert {c["category"]: c["copies"] for c in report["by_category"]}["วิทยาศาสตร์"] == 6


def test_age_is_measured_over_the_dated_stock_only():
    """A library that has not recorded half its years does not thereby have a young half."""
    report = analyse(build_items(ITEMS), older_than=2555)
    assert report["unknown_year"] == 3
    assert report["age"]["dated_copies"] == 12, "the undated three are excluded"
    assert report["age"]["older_copies"] == 4
    assert report["age"]["older_share"] == pytest.approx(33.33, abs=0.01)


def test_age_is_broken_down_by_category():
    by_category = {
        c["category"]: c["older_share"]
        for c in analyse(build_items(ITEMS), older_than=2555)["age"]["by_category"]
    }
    assert by_category["วิทยาศาสตร์"] == pytest.approx(66.67, abs=0.01)
    assert by_category["วรรณกรรม"] == 0.0
    assert "สังคม" not in by_category, "nothing in สังคม has a recorded year"


def test_age_uses_a_year_not_an_age_so_a_rerun_agrees_with_itself():
    report = analyse(build_items(ITEMS), older_than=2555)
    assert report["age"]["before"] == 2555


def test_copies_per_pupil_needs_the_roll():
    items = build_items(ITEMS)
    assert "copies_per_pupil" not in analyse(items)
    assert analyse(items, enrolled=100)["copies_per_pupil"] == 0.15


def test_gaps_state_the_shortfall_in_both_points_and_books():
    """ "You are 8 points short in science" and "that is 41 books" are the same fact, and
    only one of them can be ordered."""
    rows = gaps(build_items(ITEMS), {"วิทยาศาสตร์": 60, "วรรณกรรม": 20, "สังคม": 20})
    science = next(r for r in rows if r["category"] == "วิทยาศาสตร์")
    assert science["share"] == 40.0
    assert science["gap"] == -20.0
    assert science["copies_to_target"] == 3.0


def test_a_target_that_does_not_total_a_hundred_is_refused():
    with pytest.raises(CollectionError, match="ไม่เท่ากับ 100%"):
        gaps(build_items(ITEMS), {"วิทยาศาสตร์": 50, "วรรณกรรม": 20})


def test_a_category_held_but_not_targeted_still_appears():
    rows = gaps(build_items(ITEMS), {"วิทยาศาสตร์": 100})
    assert {r["category"] for r in rows} >= {"วิทยาศาสตร์", "วรรณกรรม", "สังคม"}
    literature = next(r for r in rows if r["category"] == "วรรณกรรม")
    assert literature["target"] == 0.0 and literature["gap"] == 40.0


def test_zero_copies_is_refused():
    with pytest.raises(CollectionError, match="เป็นไปไม่ได้"):
        build_items([{"title": "ก", "copies": 0}])


def test_an_untitled_item_is_refused():
    with pytest.raises(CollectionError, match="ไม่มีชื่อ"):
        build_items([{"title": "   "}])


def test_an_item_with_no_category_is_bucketed_rather_than_dropped():
    report = analyse(build_items([{"title": "ก", "copies": 2}]))
    assert report["by_category"][0]["category"] == "ไม่ระบุ"
