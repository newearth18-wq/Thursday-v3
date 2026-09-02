"""V7 acceptance test — pointing and speaking at once.

    The owner points at a desktop icon and says "Thursday เปิดอันนี้".

Neither half is an instruction alone. The words carry no target — "อันนี้" names nothing —
and the gesture carries no verb. Only together do they mean anything, which is what makes
this the test worth having: it is the first time two modalities have to agree before
Thursday acts.

The safety cases matter as much. A gesture must never confirm something consequential, and
an ordinary wave outside gesture mode must never be a command at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_devices.fake import FakeDeviceNode
from thursday_shared.enums import TaskState
from thursday_shared.models import UserRequest
from thursday_vision.gestures import (
    Gesture,
    GestureMode,
    GestureReading,
    GestureState,
    GestureTracker,
    HandLandmarks,
)
from thursday_vision.ports import BoundingBox
from thursday_vision.safety import check_command
from thursday_vision.screen import ScreenElement, ScreenReading, VisualReferenceResolver


def pointing_hand(at: tuple[float, float], aim: tuple[float, float]) -> HandLandmarks:
    """A hand with one finger extended, aiming from ``at`` towards ``aim``."""
    points = [at] * 21
    for tip, pip in zip(HandLandmarks.TIPS, HandLandmarks.PIPS, strict=True):
        points[tip] = (at[0], at[1] + 0.3)  # curled
        points[pip] = at
    points[3] = (at[0] + 0.01, at[1] + 0.35)  # thumb tucked, so it does not read extended
    points[HandLandmarks.INDEX_PIP] = at
    points[HandLandmarks.INDEX_TIP] = aim
    return HandLandmarks(points=points, confidence=0.95)


@pytest.fixture
def desktop() -> ScreenReading:
    """Two icons, side by side. The owner is going to point at one of them."""
    return ScreenReading(
        active_window="Desktop",
        elements=[
            ScreenElement("Chrome", BoundingBox(0.05, 0.05, 0.12, 0.12), role="icon"),
            ScreenElement("Notepad", BoundingBox(0.60, 0.60, 0.12, 0.12), role="icon"),
        ],
    )


# ------------------------------------------------------------------ the acceptance flow


def test_pointing_plus_speech_resolves_to_the_icon(desktop):
    """Neither half is an instruction alone."""
    tracker = GestureTracker()
    reading = tracker.observe([pointing_hand((0.5, 0.75), (0.63, 0.68))])
    assert reading.gesture is Gesture.POINT
    assert reading.pointing_at is not None

    reference = VisualReferenceResolver().resolve(
        utterance="Thursday เปิดอันนี้", screen=desktop, gesture=reading
    )

    assert reference is not None
    assert "Notepad" in reference.target
    assert reference.confident
    assert "pointing at it" in reference.evidence[0]


def test_pointing_elsewhere_resolves_to_the_other_icon(desktop):
    tracker = GestureTracker()
    reading = tracker.observe([pointing_hand((0.2, 0.3), (0.11, 0.12))])
    reference = VisualReferenceResolver().resolve(
        utterance="เปิดอันนี้", screen=desktop, gesture=reading
    )
    assert reference is not None
    assert "Chrome" in reference.target


async def test_the_resolved_icon_is_opened_and_verified(container, tmp_path, session_id, desktop):
    """The gesture picked the target; the rest of the system does what it always does —
    permission, device, ACT then VERIFY."""
    node = FakeDeviceNode(name="Office-PC", allowed_roots=[tmp_path])
    device = node.session()
    await container.hub.register(device)
    container.world.update(active_device_id=device.device_id, active_device_name="Office-PC")

    tracker = GestureTracker()
    reading = tracker.observe([pointing_hand((0.5, 0.75), (0.63, 0.68))])
    reference = VisualReferenceResolver().resolve(
        utterance="Thursday เปิดอันนี้", screen=desktop, gesture=reading
    )
    assert reference is not None and reference.element is not None

    # The resolved target becomes an ordinary request. Nothing about gestures reaches the
    # device layer — by the time anything is executed, this is a normal task.
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text=f"Thursday เปิด {reference.element.label}",
            device_id=device.device_id,
        )
    )

    assert "notepad" in node.adapter.running
    task = container.tasks.list()[0]
    assert task.status is TaskState.COMPLETED
    assert task.verification is not None and task.verification.passed
    assert response.verified is True


# ------------------------------------------------------------------ safety


def test_a_wave_outside_gesture_mode_opens_nothing(desktop):
    """§28. The most common false positive there is: someone waving at a person."""
    mode = GestureMode()
    for gesture in (Gesture.OPEN_PALM, Gesture.POINT, Gesture.THUMBS_UP, Gesture.SWIPE_LEFT):
        assert mode.observe(gesture) is None
    assert mode.state is GestureState.OFF


def test_a_gesture_cannot_confirm_deleting_a_file():
    """§29 — a thumbs-up is a hand shape, and hand shapes are misread."""
    verdict = check_command(Gesture.THUMBS_UP, confidence=0.95, action="file.delete")
    assert not verdict.allowed
    assert verdict.needs_words


def test_a_gesture_can_confirm_something_harmless():
    assert check_command(Gesture.THUMBS_UP, confidence=0.9, action="app.open").allowed


def test_holding_a_thumbs_up_is_not_thirty_confirmations():
    """A held gesture is one intention, and the frame rate must not turn it into many.

    Thirty frames at 33ms is a second of holding still — how anyone actually makes a
    gesture. Without the cooldown that is thirty confirmations of the same thing. With it,
    a second yields at most two: one immediately, one when the cooldown lapses. The second
    is honest — a full second is long enough to be a deliberate repeat — so this asserts a
    small bound rather than exactly one, which would be asserting the arithmetic of the
    cooldown constant rather than the property that matters.
    """
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)

    fired = [
        mode.observe(
            GestureReading(gesture=Gesture.THUMBS_UP, confidence=0.9),
            now=start + timedelta(milliseconds=step * 33),
        )
        for step in range(30)
    ]
    commands = [f for f in fired if f is not None]
    assert commands and all(c is Gesture.THUMBS_UP for c in commands)
    assert len(commands) <= 2

    # And the first one is immediate — a gesture that only registers after a delay reads
    # as ignored, and the owner repeats it.
    assert fired[0] is Gesture.THUMBS_UP


def test_a_low_confidence_point_does_not_steer_the_resolution(desktop):
    """A hand the tracker is guessing about must not silently outrank the mouse."""
    unsure = GestureReading(gesture=Gesture.POINT, confidence=0.3, pointing_at=(0.63, 0.68))
    reference = VisualReferenceResolver().resolve(
        utterance="เปิดอันนี้", screen=desktop, gesture=unsure
    )
    # Two icons and no usable signal: nothing is confidently the target.
    assert reference is None or not reference.confident


def test_gesture_mode_closes_itself_and_the_owner_can_close_it(desktop):
    mode = GestureMode()
    start = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
    mode.open(now=start)
    assert mode.observe(Gesture.POINT, now=start) is Gesture.POINT

    # Idle for longer than the timeout.
    assert mode.observe(Gesture.POINT, now=start + timedelta(seconds=30)) is None
    assert mode.state is GestureState.OFF

    mode.open()
    mode.close()
    assert not mode.watching
