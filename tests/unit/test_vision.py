"""Spatial memory and gesture mode (§25–29)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from thursday.vision.gestures import (
    ACTIVATION_GESTURE,
    Gesture,
    GestureMode,
    HandLandmarks,
    classify,
)
from thursday.vision.spatial import SpatialMemory

# ------------------------------------------------------------------ spatial memory


@pytest.fixture
def spatial() -> SpatialMemory:
    return SpatialMemory()


def test_the_latest_sighting_wins(spatial):
    # Anchored on now, because observations expire after a week by design.
    base = datetime.now(UTC) - timedelta(hours=12)
    spatial.record("keys", confidence=0.8, location_context="kitchen", seen_at=base)
    spatial.record(
        "keys", confidence=0.92, location_context="desk", seen_at=base + timedelta(hours=9)
    )

    latest = spatial.last_seen("keys")
    assert latest is not None
    assert latest.location_context == "desk"
    assert latest.confidence == pytest.approx(0.92)


def test_an_answer_is_framed_as_a_sighting_not_a_guarantee(spatial):
    """§26 — Thursday says where it *saw* the thing, and says so explicitly."""
    observation = spatial.record(
        "keys",
        confidence=0.92,
        location_context="โต๊ะทำงาน",
        seen_at=datetime.now(UTC).replace(hour=18, minute=22) - timedelta(days=1),
    )
    thai = observation.describe("th")
    assert "18:22" in thai and "0.92" in thai
    assert "ยังไม่ยืนยัน" in thai  # not a promise about the present

    english = observation.describe("en")
    assert "last seen" in english and "not a guarantee" in english


def test_low_confidence_sightings_are_filtered_out(spatial):
    spatial.record("keys", confidence=0.2, location_context="somewhere")
    assert spatial.last_seen("keys", min_confidence=0.5) is None
    assert spatial.last_seen("keys", min_confidence=0.1) is not None


def test_observations_expire_on_their_own(spatial):
    spatial.record("keys", confidence=0.9, seen_at=datetime.now(UTC) - timedelta(days=30))
    assert spatial.last_seen("keys") is None
    assert len(spatial) == 0


def test_the_owner_can_wipe_what_was_seen(spatial):
    """Part of the privacy controls (§68): 'forget what you saw' must actually work."""
    spatial.record("wallet", confidence=0.9)
    spatial.record("keys", confidence=0.9)
    assert spatial.forget_all() == 2
    assert spatial.last_seen("keys") is None


def test_history_is_ordered_newest_first(spatial):
    base = datetime.now(UTC) - timedelta(hours=6)
    for hour in range(3):
        spatial.record(
            "keys",
            confidence=0.9,
            location_context=f"place-{hour}",
            seen_at=base + timedelta(hours=hour),
        )
    assert [o.location_context for o in spatial.history("keys")] == [
        "place-2",
        "place-1",
        "place-0",
    ]


# ------------------------------------------------------------------ gestures


def hand(extended: list[bool]) -> HandLandmarks:
    """Build landmarks whose fingers read as the requested extension pattern."""
    points = [(0.5, 0.5)] * 21
    for tip, pip, is_up in zip(HandLandmarks.TIPS, HandLandmarks.PIPS, extended, strict=True):
        if tip == 4:  # the thumb opposes sideways
            points[tip] = (0.9 if is_up else 0.5, 0.3)
            points[pip] = (0.5, 0.5)
        else:
            points[tip] = (0.5, 0.2 if is_up else 0.8)
            points[pip] = (0.5, 0.5)
    return HandLandmarks(points=points)


@pytest.mark.parametrize(
    ("fingers", "expected"),
    [
        ([True, True, True, True, True], Gesture.OPEN_PALM),
        ([False, True, True, False, False], Gesture.PEACE),
        ([False, True, False, False, False], Gesture.POINT),
        ([False, False, False, False, False], Gesture.PINCH),
    ],
)
def test_landmark_patterns_classify(fingers, expected):
    gesture, confidence = classify([hand(fingers)])
    assert gesture is expected
    assert confidence > 0.5


def test_no_hands_is_not_a_gesture():
    assert classify([]) == (Gesture.NONE, 0.0)


def test_two_hands_read_as_zoom():
    gesture, _ = classify([hand([True] * 5), hand([True] * 5)])
    assert gesture is Gesture.TWO_HAND_ZOOM


def test_gestures_are_ignored_until_the_mode_is_open():
    """§28 — an ordinary wave must never be a command."""
    mode = GestureMode()
    assert mode.observe(Gesture.OPEN_PALM) is None
    assert not mode.active

    assert mode.observe(Gesture(ACTIVATION_GESTURE)) is None  # the activation opens it
    assert mode.active
    assert mode.observe(Gesture.POINT) is Gesture.POINT


def test_a_wake_word_can_open_gesture_mode_too():
    mode = GestureMode()
    mode.observe(Gesture.NONE, wake_word=True)
    assert mode.active


def test_gesture_mode_closes_itself_when_idle():
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)
    assert mode.observe(Gesture.POINT, now=start + timedelta(seconds=5)) is Gesture.POINT

    later = start + timedelta(seconds=30)
    assert mode.observe(Gesture.POINT, now=later) is None
    assert not mode.active


def test_interaction_keeps_the_mode_alive():
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)
    for step in range(1, 5):
        now = start + timedelta(seconds=step * 5)
        assert mode.observe(Gesture.POINT, now=now) is Gesture.POINT
    assert mode.active
