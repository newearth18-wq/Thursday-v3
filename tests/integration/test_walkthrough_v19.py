"""The §13 walkthrough: a lesson points at a control that exists (V19).

§23 carried this as an open gap, and the reason it is worth building carefully is the reason
it was worth naming: an arrow is a claim about where something *is*. Point one at the wrong
place once and the owner stops trusting the next.

The way that goes wrong is not a bug in the arrow. It is a lesson naming a control that was
renamed in the interface three sprints later — the same drift ADR 0065 was written for, in a
place where nothing would notice.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from thursday_core.lessons import LESSONS, LESSONS_BY_ID

DESKTOP = Path("apps/desktop/src")


def marked_in_the_app() -> set[str]:
    """Every control the desktop app has marked as pointable."""
    names: set[str] = set()
    for source in [*DESKTOP.rglob("*.tsx"), *DESKTOP.rglob("*.ts")]:
        if source.name.endswith((".test.tsx", ".test.ts")):
            continue
        names |= set(re.findall(r'data-teach="([^"]+)"', source.read_text(encoding="utf-8")))
    return names


def pointed_at_by_lessons() -> set[str]:
    return {step.points_at for lesson in LESSONS for step in lesson.steps if step.points_at}


def test_the_app_marks_at_least_one_control():
    assert marked_in_the_app(), (
        "no control carries data-teach; this test is looking in the wrong place"
    )


def test_every_control_a_lesson_points_at_exists_in_the_app():
    """The drift this guards against: a lesson keeps pointing at `stop-all` after the button
    was renamed, and the walkthrough draws an arrow at nothing."""
    missing = pointed_at_by_lessons() - marked_in_the_app()
    assert missing == set(), f"lessons point at controls the app does not mark: {sorted(missing)}"


@pytest.mark.parametrize(
    ("lesson_id", "expected"),
    [("say-something", "conversation-input"), ("how-to-stop", "stop-all")],
)
def test_the_lessons_that_are_about_a_control_name_it(lesson_id, expected):
    """`how-to-stop` says "there is a button". A sentence about a button the owner cannot
    find is worse than no sentence."""
    lesson = LESSONS_BY_ID[lesson_id]
    assert any(step.points_at == expected for step in lesson.steps)


def test_a_step_about_what_to_say_points_at_nothing():
    """Empty is the honest answer for a step that is about words rather than a place, and
    the interface renders it as no arrow rather than an arrow somewhere plausible."""
    remember = LESSONS_BY_ID["remember-this"]
    assert all(step.points_at == "" for step in remember.steps)


async def test_the_step_payload_carries_the_target(container):
    """The interface cannot point at anything it is not told about."""
    result = container.lessons.start(container, "say-something")
    assert result is not None
    assert result.next_points_at == "conversation-input"


async def test_a_lesson_with_nothing_to_point_at_says_so_with_an_empty_string(container):
    """Not a missing key, not null — the field is always there, so the interface has one
    shape to handle rather than two."""
    result = container.lessons.start(container, "remember-this")
    assert result is not None
    assert result.next_points_at == ""
