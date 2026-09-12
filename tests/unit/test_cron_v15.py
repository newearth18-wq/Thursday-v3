"""Five-field cron: parsing, matching, and the rule libraries disagree about (V15)."""

from __future__ import annotations

from datetime import datetime

import pytest
from thursday_automation.cron import CronRefused, describe, parse, zone

# 2026-09-07 is a Monday. Every date below is checked against that anchor.
MON = datetime(2026, 9, 7, 7, 30)
TUE = datetime(2026, 9, 8, 7, 30)
SAT = datetime(2026, 9, 12, 7, 30)
SUN = datetime(2026, 9, 13, 7, 30)


def test_a_plain_daily_time_matches_only_that_minute():
    schedule = parse("30 7 * * *")
    assert schedule.matches(MON)
    assert not schedule.matches(MON.replace(minute=31))
    assert not schedule.matches(MON.replace(hour=8))


def test_seconds_are_ignored_never_rounded():
    """07:30:59 is still 07:30. Rounding would fire a 07:30 rule at 07:31."""
    schedule = parse("30 7 * * *")
    assert schedule.matches(MON.replace(second=59, microsecond=999_999))
    assert not schedule.matches(MON.replace(minute=29, second=59))


def test_weekday_ranges_and_names_agree():
    by_number = parse("30 7 * * 1-5")
    by_name = parse("30 7 * * MON-FRI")
    for when in (MON, TUE, SAT, SUN):
        assert by_number.matches(when) == by_name.matches(when)
    assert by_number.matches(MON) and not by_number.matches(SAT)


def test_sunday_is_both_zero_and_seven():
    """Every crontab accepts 7 for Sunday. A rule written that way must not be refused as
    out of range, and must not land on Saturday."""
    assert parse("30 7 * * 0").matches(SUN)
    assert parse("30 7 * * 7").matches(SUN)
    assert not parse("30 7 * * 7").matches(SAT)


def test_steps_and_lists():
    every_quarter = parse("*/15 * * * *")
    assert {m for m in range(60) if every_quarter.matches(MON.replace(minute=m))} == {0, 15, 30, 45}
    listed = parse("0,30 8,17 * * *")
    assert listed.matches(MON.replace(hour=17, minute=30))
    assert not listed.matches(MON.replace(hour=17, minute=15))


def test_a_range_with_a_step():
    schedule = parse("0 9-17/4 * * *")
    hours = {h for h in range(24) if schedule.matches(MON.replace(hour=h, minute=0))}
    assert hours == {9, 13, 17}


# ------------------------------------------------- the rule everybody gets wrong once


def test_day_and_weekday_together_mean_or_not_and():
    """POSIX: with both restricted, cron fires when *either* matches. Read as "and", this
    rule would fire about once a year and the owner would find out by it not happening."""
    schedule = parse("30 7 1 * MON")
    assert schedule.matches(datetime(2026, 9, 1, 7, 30)), "the 1st, which is a Tuesday"
    assert schedule.matches(MON), "a Monday that is not the 1st"
    assert not schedule.matches(TUE), "neither the 1st nor a Monday"


def test_only_one_of_them_restricted_is_a_plain_and():
    """The OR applies only when both are narrowed. `30 7 1 * *` is the 1st, full stop."""
    first = parse("30 7 1 * *")
    assert first.matches(datetime(2026, 9, 1, 7, 30))
    assert not first.matches(MON)


def test_a_full_range_still_counts_as_restricted():
    """`1-31` is every day, but it was *written*, so the OR rule applies — which is what a
    crontab does. Deciding by set size instead would change behaviour based on spelling."""
    schedule = parse("30 7 1-31 * MON")
    assert schedule.day_restricted and schedule.weekday_restricted
    assert schedule.matches(TUE)


# --------------------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    "expression",
    ["", "0 9 * *", "0 9 * * * *", "60 9 * * *", "0 24 * * *", "0 9 32 * *", "0 9 * 13 *"],
)
def test_an_expression_that_cannot_mean_what_it_says_is_refused(expression):
    with pytest.raises(CronRefused):
        parse(expression)


def test_a_backwards_range_is_refused_rather_than_wrapped():
    """FRI-MON could be the weekend or a typo for MON-FRI. Guessing changes when a rule
    runs, silently."""
    with pytest.raises(CronRefused, match="เริ่มหลังจบ"):
        parse("0 9 * * FRI-MON")


def test_a_zero_step_is_refused():
    with pytest.raises(CronRefused, match="ก้าว"):
        parse("*/0 * * * *")


def test_nonsense_is_refused_not_narrowed_to_everything():
    """The dangerous failure is not an error — it is a rule the owner believes runs at
    07:30 and which actually runs every minute."""
    with pytest.raises(CronRefused):
        parse("half past seven * * * *")


def test_an_unknown_timezone_is_refused_rather_than_silently_utc():
    """Falling back to UTC moves every schedule in Bangkok by seven hours and says nothing."""
    assert zone("Asia/Bangkok").key == "Asia/Bangkok"
    with pytest.raises(CronRefused, match="เขตเวลา"):
        zone("Mars/Olympus_Mons")


# -------------------------------------------------------------------- plain language


def test_the_common_shapes_are_described_exactly():
    assert describe("30 7 * * 1-5") == "07:30 วันจันทร์ถึงศุกร์"
    assert describe("0 9 * * *") == "09:00 ทุกวัน"
    assert describe("0 10 * * 0,6") == "10:00 วันเสาร์อาทิตย์"
    assert describe("15 * * * *") == "ทุกชั่วโมง นาทีที่ 15"


def test_the_or_rule_is_said_out_loud_in_the_description():
    assert "หรือวัน" in describe("0 0 1 * MON")


def test_an_unusual_shape_is_not_given_a_confident_sentence():
    """A wrong sentence about when a rule runs is worse than no sentence."""
    assert describe("0,30 9-17 * * *") == "ตามรูปแบบ 0,30 9-17 * * *"
