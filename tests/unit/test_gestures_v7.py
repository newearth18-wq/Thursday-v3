"""Hand tracking and gesture safety (V7).

Most of these are about *not* acting. That is the shape of the problem: a recogniser good
enough to be useful is still wrong often enough that acting on every reading would be
unusable, and the gestures people make while talking to someone else in the room are
indistinguishable from the ones they make at a computer.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from thursday_shared.enums import PermissionLevel, RiskLevel
from thursday_vision.gestures import (
    ACTIVATION_GESTURE,
    Gesture,
    GestureMode,
    GestureReading,
    GestureState,
    GestureTracker,
    HandLandmarks,
    classify,
)
from thursday_vision.safety import (
    GestureVerdict,
    check_command,
    is_consequential,
    may_confirm,
)


def hand(
    extended: list[bool],
    *,
    at: tuple[float, float] = (0.5, 0.5),
    pinch: float = 0.5,
    when: datetime | None = None,
) -> HandLandmarks:
    """Landmarks whose fingers read as the requested pattern, anchored at a wrist."""
    points = [at] * 21
    for tip, pip, is_up in zip(HandLandmarks.TIPS, HandLandmarks.PIPS, extended, strict=True):
        if tip == HandLandmarks.THUMB_TIP:
            points[tip] = (at[0] + (0.4 if is_up else 0.0), at[1] - 0.2)
            points[pip] = at
        else:
            points[tip] = (at[0], at[1] - 0.3 if is_up else at[1] + 0.3)
            points[pip] = at
    # Place the thumb tip a chosen distance from the index tip — that distance is what
    # separates a pinch from a fist. The thumb's own PIP moves with it, so a thumb held
    # away from the palm does not also read as *extended*: otherwise every fist built here
    # would classify as a thumbs-up, which is a fault in the fixture and not in the code.
    index = points[HandLandmarks.INDEX_TIP]
    thumb_tip = (index[0] + pinch, index[1])
    points[HandLandmarks.THUMB_TIP] = thumb_tip
    if not extended[0]:
        points[3] = (thumb_tip[0] + 0.01, thumb_tip[1] + 0.05)
    return HandLandmarks(points=points, at=when or datetime.now(UTC))


# ------------------------------------------------------------------ pinch is not a fist


def test_a_closed_hand_is_a_fist_not_a_pinch():
    """The bug this replaces: every resting hand read as a click."""
    gesture, _ = classify([hand([False] * 5, pinch=0.4)])
    assert gesture is Gesture.FIST


def test_touching_finger_and_thumb_is_a_pinch():
    gesture, confidence = classify([hand([False] * 5, pinch=0.02)])
    assert gesture is Gesture.PINCH
    assert confidence > 0.7


def test_a_firmer_pinch_is_more_confident():
    _, loose = classify([hand([False] * 5, pinch=0.055)])
    _, firm = classify([hand([False] * 5, pinch=0.005)])
    assert firm > loose


# ------------------------------------------------------------------ pointing direction


def test_pointing_direction_is_a_unit_vector_along_the_finger():
    points = [(0.5, 0.5)] * 21
    points[HandLandmarks.INDEX_PIP] = (0.5, 0.5)
    points[HandLandmarks.INDEX_TIP] = (0.5, 0.3)  # straight up
    landmarks = HandLandmarks(points=points)

    direction = landmarks.pointing_direction()
    assert direction is not None
    assert math.isclose(math.hypot(*direction), 1.0, rel_tol=1e-6)
    assert direction[1] < 0  # upward


def test_aim_projects_past_the_fingertip():
    """Where the finger *aims*, not where its tip happens to be — which is what resolves
    "that one over there" on a screen the hand is not touching."""
    points = [(0.5, 0.5)] * 21
    points[HandLandmarks.INDEX_PIP] = (0.5, 0.6)
    points[HandLandmarks.INDEX_TIP] = (0.5, 0.5)
    aim = HandLandmarks(points=points).aim_at(0.3)
    assert aim is not None
    assert aim[1] < 0.5


def test_aim_stays_inside_the_frame():
    points = [(0.5, 0.5)] * 21
    points[HandLandmarks.INDEX_PIP] = (0.5, 0.9)
    points[HandLandmarks.INDEX_TIP] = (0.5, 0.1)
    aim = HandLandmarks(points=points).aim_at(5.0)
    assert aim == (0.5, 0.0)


# ------------------------------------------------------------------ motion


def test_a_moving_open_hand_is_a_swipe():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(5):
        reading = tracker.observe(
            [hand([True] * 5, at=(0.2 + step * 0.1, 0.5))],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.SWIPE_RIGHT
    assert reading.command == "next"


def test_swiping_the_other_way_is_the_other_command():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(5):
        reading = tracker.observe(
            [hand([True] * 5, at=(0.8 - step * 0.1, 0.5))],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.SWIPE_LEFT
    assert reading.command == "previous"


def test_a_hand_drifting_slowly_is_not_a_swipe():
    """A hand crossing the frame over five seconds is someone reaching for their coffee."""
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(5):
        reading = tracker.observe(
            [hand([True] * 5, at=(0.2 + step * 0.1, 0.5))],
            now=start + timedelta(seconds=step * 2),
        )
    assert reading.gesture is not Gesture.SWIPE_RIGHT


def test_a_moving_pinch_is_a_drag():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(4):
        reading = tracker.observe(
            [hand([False] * 5, at=(0.3 + step * 0.05, 0.5), pinch=0.01)],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.DRAG
    assert reading.travel > 0


def test_a_still_pinch_stays_a_pinch():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(4):
        reading = tracker.observe(
            [hand([False] * 5, at=(0.5, 0.5), pinch=0.01)],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.PINCH


def test_two_hands_moving_apart_is_zoom_in():
    """ "Two hands moved" is not an instruction; "the hands moved apart" is."""
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(4):
        gap = 0.1 + step * 0.1
        reading = tracker.observe(
            [hand([True] * 5, at=(0.5 - gap, 0.5)), hand([True] * 5, at=(0.5 + gap, 0.5))],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.ZOOM_IN


def test_two_hands_coming_together_is_zoom_out():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(4):
        gap = 0.4 - step * 0.1
        reading = tracker.observe(
            [hand([True] * 5, at=(0.5 - gap, 0.5)), hand([True] * 5, at=(0.5 + gap, 0.5))],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.ZOOM_OUT


def test_two_still_hands_are_not_a_zoom():
    tracker = GestureTracker()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    reading = GestureReading()
    for step in range(4):
        reading = tracker.observe(
            [hand([True] * 5, at=(0.3, 0.5)), hand([True] * 5, at=(0.7, 0.5))],
            now=start + timedelta(milliseconds=step * 100),
        )
    assert reading.gesture is Gesture.NONE


# ------------------------------------------------------------------ the four states


def test_an_ordinary_wave_is_never_a_command():
    """§28. Outside ACTIVE, only the activation gesture has any effect."""
    mode = GestureMode()
    assert mode.state is GestureState.OFF
    assert mode.observe(Gesture.OPEN_PALM) is None
    assert mode.observe(Gesture.THUMBS_UP) is None
    assert not mode.active


def test_arming_watches_for_the_activation_and_nothing_else():
    mode = GestureMode()
    mode.arm()
    assert mode.state is GestureState.ARMED
    assert mode.watching
    assert mode.observe(Gesture.THUMBS_UP) is None

    mode.observe(Gesture(ACTIVATION_GESTURE))
    assert mode.state is GestureState.ACTIVE


def test_one_gesture_is_one_command():
    """At thirty frames a second a half-second thumbs-up is fifteen frames. Without a
    cooldown that is fifteen confirmations."""
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)

    fired = [
        mode.observe(Gesture.THUMBS_UP, now=start + timedelta(milliseconds=step * 33))
        for step in range(15)
    ]
    assert [f for f in fired if f is not None] == [Gesture.THUMBS_UP]


def test_the_next_command_lands_after_the_cooldown():
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)

    assert mode.observe(Gesture.POINT, now=start) is Gesture.POINT
    assert mode.observe(Gesture.POINT, now=start + timedelta(seconds=1)) is Gesture.POINT


def test_holding_a_gesture_keeps_the_mode_alive():
    """The owner is plainly still there, even while nothing is being read."""
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)
    mode.observe(Gesture.POINT, now=start)

    mode.observe(Gesture.POINT, now=start + timedelta(milliseconds=100))
    assert mode.last_interaction > start


def test_a_reading_can_be_fed_in_directly():
    mode = GestureMode()
    mode.open()
    assert mode.observe(GestureReading(gesture=Gesture.SWIPE_LEFT, confidence=0.8)) is (
        Gesture.SWIPE_LEFT
    )


# ------------------------------------------------------------------ safety


@pytest.mark.parametrize(
    "action",
    [
        "file.delete",
        "system.power",
        "shell.run",
        "security.disable",
        "email.send",
        "browser.submit",
        "app.install",
        "credential.export",
    ],
)
def test_a_gesture_can_never_confirm_something_consequential(action):
    """A thumbs-up is a hand shape, and hand shapes are misread."""
    assert is_consequential(action)
    verdict = may_confirm(Gesture.THUMBS_UP, action=action, confidence=0.95)
    assert not verdict.allowed
    assert verdict.needs_words


def test_a_new_verb_under_a_blocked_namespace_is_covered():
    """Prefix-matched like the action policy, so nobody has to remember to add it."""
    assert is_consequential("security.firewall.disable")
    assert is_consequential("system.something.new")


def test_a_harmless_reversible_action_can_be_confirmed_by_gesture():
    verdict = may_confirm(
        Gesture.THUMBS_UP,
        action="app.open",
        confidence=0.9,
        level=PermissionLevel.OPEN,
        risk=RiskLevel.LOW,
    )
    assert verdict.allowed


def test_an_unlisted_action_is_still_caught_by_its_own_properties():
    """Reached from the action's level and risk rather than its name, so something nobody
    thought to list is still covered."""
    verdict = may_confirm(
        Gesture.THUMBS_UP,
        action="something.nobody.listed",
        confidence=0.9,
        level=PermissionLevel.EXTERNAL,
    )
    assert not verdict.allowed
    assert verdict.needs_words


def test_an_irreversible_action_needs_more_than_a_gesture():
    """Undo is what makes a misread gesture survivable."""
    verdict = may_confirm(Gesture.THUMBS_UP, action="app.open", confidence=0.9, reversible=False)
    assert not verdict.allowed


def test_a_low_confidence_gesture_is_not_acted_on_at_all():
    assert not check_command(Gesture.SWIPE_LEFT, confidence=0.3)
    assert not may_confirm(Gesture.THUMBS_UP, confidence=0.4)


def test_navigation_carries_no_risk():
    """A swipe that goes the wrong way costs one swipe back."""
    assert check_command(Gesture.SWIPE_LEFT, confidence=0.8)
    assert check_command(Gesture.POINT, confidence=0.9)


def test_cancelling_is_always_allowed():
    """Refusing to act is the safe direction, and making "stop" harder than "go" would be
    exactly backwards."""
    verdict = check_command(Gesture.THUMBS_DOWN, confidence=0.7, action="file.delete")
    assert verdict.allowed


def test_a_gesture_cannot_trigger_a_consequential_action_either():
    """Not only confirmations: a swipe mapped onto a delete is the same failure."""
    verdict = check_command(Gesture.SWIPE_LEFT, confidence=0.9, action="file.delete")
    assert not verdict.allowed
    assert verdict.needs_words


def test_a_verdict_is_falsy_when_it_refuses():
    assert not GestureVerdict(False, "no")
    assert GestureVerdict(True, "yes")
