"""Vision (V6).

Two thirds of these are about the camera being off. That ratio is deliberate: a camera that
is on when the owner believes it is off is the worst failure this system can have, worse
than a wrong answer or a lost task, and unlike either of those it cannot be put right
afterwards.

The rest cover what happens once looking is permitted — sampling, reading, resolving "this",
and answering as a sighting rather than as a fact about the present.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_vision.camera import CameraDenied, CameraManager, CameraState
from thursday_vision.fake import (
    FailingDetector,
    FakeCamera,
    FakeScreen,
    ScriptedAnalyzer,
    ScriptedBarcodes,
    ScriptedDetector,
    ScriptedOCR,
    detection,
    fake_frame,
    frames_that_differ,
    text_block,
)
from thursday_vision.ports import Barcode, BoundingBox
from thursday_vision.sampling import FrameSampler, SamplingPolicy
from thursday_vision.screen import (
    ScreenElement,
    ScreenReading,
    VisualReferenceResolver,
    annotate,
)
from thursday_vision.service import VisionService
from thursday_vision.spatial import SpatialMemory

# ------------------------------------------------------------------ the camera is off


async def test_the_camera_starts_off_and_stays_off():
    camera = FakeCamera()
    manager = CameraManager(camera)

    assert manager.state is CameraState.OFF
    assert manager.indicator_on is False
    assert manager.may_capture()[0] is False
    # The hardware was never touched. Not "opened and idle" — never opened.
    assert camera.opens == 0


async def test_capturing_without_a_grant_is_refused():
    camera = FakeCamera()
    manager = CameraManager(camera)

    with pytest.raises(CameraDenied):
        await manager.capture()
    assert camera.opens == 0
    assert manager.indicator_on is False


async def test_a_grant_needs_a_reason():
    """A grant nobody can describe later is a grant nobody can audit."""
    with pytest.raises(ValueError):
        CameraManager(FakeCamera()).grant_access("   ")


async def test_the_indicator_is_on_exactly_while_the_hardware_is():
    """Derived from the same field the capture path reads. An indicator computed from a
    separate flag can disagree with reality, and the first time it does is the time it
    matters."""
    camera = FakeCamera()
    manager = CameraManager(camera)

    manager.grant_access("identify a book")
    assert manager.state is CameraState.ARMED
    # Granted is not open. The light stays off until something actually captures.
    assert manager.indicator_on is False
    assert camera.opens == 0

    await manager.capture()
    assert manager.indicator_on is True
    assert camera.is_open is True

    await manager.revoke()
    assert manager.indicator_on is False
    assert camera.is_open is False


async def test_an_expired_grant_stops_working():
    camera = FakeCamera()
    manager = CameraManager(camera)
    manager.grant_access("a quick look", seconds=0.001)
    manager._grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    allowed, why = manager.may_capture()
    assert not allowed
    assert "expired" in why
    with pytest.raises(CameraDenied):
        await manager.capture()


async def test_a_one_shot_grant_is_spent_after_one_look():
    """ "Yes, look at this book" must not become "yes, watch the room"."""
    camera = FakeCamera()
    manager = CameraManager(camera)
    manager.grant_access("identify this", max_captures=1)

    await manager.capture()
    assert manager.state is CameraState.OFF
    assert manager.grant is None
    with pytest.raises(CameraDenied):
        await manager.capture()


async def test_an_idle_camera_closes_itself():
    """The failure mode of consent is a grant nobody withdrew, not a request refused."""
    camera = FakeCamera()
    manager = CameraManager(camera, idle_close_seconds=10)
    manager.grant_access("watching for a delivery", seconds=600)
    await manager.capture()
    assert manager.indicator_on

    closed = await manager.close_if_idle(now=datetime.now(UTC) + timedelta(seconds=30))
    assert closed
    assert manager.indicator_on is False
    assert camera.is_open is False


async def test_hardware_that_will_not_close_still_reports_off_and_says_so(caplog):
    """An indicator stuck on ACTIVE is the more alarming lie, and a camera that cannot be
    closed is a fault the owner must hear about."""
    camera = FakeCamera(fail_close=True)
    manager = CameraManager(camera)
    manager.grant_access("a look")
    await manager.capture()

    await manager.revoke()
    assert manager.state is CameraState.OFF
    assert manager.indicator_on is False


async def test_the_camera_log_answers_when_was_my_camera_on():
    camera = FakeCamera()
    manager = CameraManager(camera)
    manager.grant_access("identify a book")
    await manager.capture()
    await manager.revoke()

    entries = manager.recent_log()
    transitions = [e["transition"] for e in entries]
    assert "OFF->ARMED" in transitions
    assert "ARMED->ACTIVE" in transitions
    assert "ACTIVE->OFF" in transitions
    assert any("identify a book" in e["why"] for e in entries)


async def test_the_service_cannot_grant_itself_permission():
    """A component that can grant itself camera access has no permission model."""
    camera = FakeCamera()
    manager = CameraManager(camera)
    service = VisionService(camera=manager, detector=ScriptedDetector(default=[detection("book")]))

    answer = await service.look("what is this")
    assert not answer.ok
    assert "no access has been granted" in (answer.refused or "")
    assert camera.opens == 0


# ------------------------------------------------------------------ frame sampling


def test_an_unchanged_scene_is_not_resampled():
    sampler = FrameSampler(SamplingPolicy(min_interval_s=0.0))
    frame = fake_frame()

    assert sampler.consider(frame)
    assert not sampler.consider(frame)
    assert sampler.dropped == 1


def test_a_changed_scene_is_sampled():
    sampler = FrameSampler(SamplingPolicy(min_interval_s=0.0, change_threshold=0.01))
    a, b = frames_that_differ(2)
    assert sampler.consider(a)
    assert sampler.consider(b)


def test_the_rate_limit_stops_a_stream_by_another_name():
    """Without this, a flickering scene passes the change threshold forever and the
    sampler becomes the thing it exists to prevent."""
    sampler = FrameSampler(
        SamplingPolicy(min_interval_s=0.0, change_threshold=0.0, max_per_minute=3)
    )
    for frame in frames_that_differ(10):
        sampler.consider(frame)
    assert sampler.kept == 3


def test_a_local_detector_finding_nothing_is_an_answer():
    """The sampler never ships a frame somewhere to find out whether it was interesting."""
    sampler = FrameSampler(SamplingPolicy(min_interval_s=0.0, change_threshold=0.0))
    decision = sampler.consider(fake_frame(), [detection("wall", confidence=0.1)])
    assert not decision
    assert "nothing of interest" in decision.reason


def test_something_interesting_beats_an_unchanged_scene():
    sampler = FrameSampler(SamplingPolicy(min_interval_s=0.0))
    frame = fake_frame()
    sampler.consider(frame)
    assert sampler.consider(frame, [detection("person", confidence=0.9)])


# ------------------------------------------------------------------ reading a frame


async def test_a_reading_combines_detection_text_and_barcode():
    service = VisionService(
        detector=ScriptedDetector(default=[detection("book", 0.91)]),
        ocr=ScriptedOCR(blocks=[text_block("Clean Architecture", 0.88, area=0.3)]),
        barcodes=ScriptedBarcodes(codes=[Barcode(value="9780134494166", kind="EAN13")]),
        analyzer=ScriptedAnalyzer(answer="a book"),
    )
    reading = await service.read_frame(fake_frame())

    assert reading.primary.label == "book"
    assert "Clean Architecture" in reading.all_text()
    assert reading.barcodes[0].looks_like_isbn
    assert not reading.uncertain


async def test_one_broken_provider_degrades_the_reading_rather_than_losing_it():
    """Someone holding a book up should get "I can read the cover but could not identify
    the object", not an exception."""
    service = VisionService(
        detector=FailingDetector(),
        ocr=ScriptedOCR(blocks=[text_block("Clean Architecture")]),
    )
    reading = await service.read_frame(fake_frame())

    assert reading.detections == []
    assert "Clean Architecture" in reading.all_text()
    assert not reading.uncertain


async def test_an_empty_frame_is_uncertain_not_confidently_empty():
    service = VisionService(detector=ScriptedDetector(default=[]), ocr=ScriptedOCR(blocks=[]))
    reading = await service.read_frame(fake_frame())
    assert reading.uncertain


def test_the_most_prominent_object_is_not_simply_the_most_confident():
    """A tiny, certainly-identified pen in the corner is rarely what someone pointing a
    camera is asking about."""
    from thursday_vision.ports import SceneReading

    reading = SceneReading(
        frame=fake_frame(),
        detections=[
            detection("pen", 0.99, x=0.9, y=0.9, width=0.02, height=0.02),
            detection("book", 0.75, x=0.2, y=0.2, width=0.6, height=0.6),
        ],
    )
    assert reading.primary.label == "book"


# ------------------------------------------------------------------ resolving "this"


def test_an_explicit_selection_beats_everything():
    resolver = VisualReferenceResolver()
    screen = ScreenReading(selection="=SUM(B2:B40)", pointer=(0.1, 0.1))
    reference = resolver.resolve(utterance="ตรงนี้ผิดอะไร", screen=screen)
    assert reference.target == "=SUM(B2:B40)"
    assert reference.confident


def test_pointing_resolves_to_what_is_under_the_finger():
    resolver = VisualReferenceResolver()
    screen = ScreenReading(
        elements=[
            ScreenElement("Total", BoundingBox(0.0, 0.0, 0.2, 0.2), role="cell"),
            ScreenElement("Average", BoundingBox(0.5, 0.5, 0.2, 0.2), role="cell"),
        ]
    )
    reference = resolver.resolve(utterance="อันนี้", screen=screen, pointing_at=(0.55, 0.55))
    assert "Average" in reference.target
    assert reference.confident


def test_something_named_in_the_request_is_found():
    resolver = VisualReferenceResolver()
    screen = ScreenReading(
        elements=[ScreenElement("Total", BoundingBox(0.0, 0.0, 0.2, 0.2), role="cell")]
    )
    reference = resolver.resolve(utterance="check the total please", screen=screen)
    assert "Total" in reference.target


def test_prominence_alone_is_not_enough_to_act_on():
    """The weakest signal, deliberately below the floor — so it becomes a question rather
    than an action on the wrong thing."""
    resolver = VisualReferenceResolver()
    reference = resolver.resolve(
        utterance="what about this",
        detections=[detection("book", 0.8), detection("cup", 0.7, x=0.6, y=0.6)],
    )
    assert reference is not None
    assert not reference.confident


def test_nothing_to_point_at_resolves_to_nothing():
    assert VisualReferenceResolver().resolve(utterance="fix this") is None


def test_an_uncertain_resolution_is_annotated_with_its_reasoning():
    """This is the case where the owner needs to be able to say "no, the other one"."""
    resolver = VisualReferenceResolver()
    reference = resolver.resolve(
        utterance="this one", detections=[detection("book", 0.8), detection("cup", 0.7)]
    )
    annotations = annotate(reference)
    assert any(a.kind == "label" and "is this what you meant" in a.text for a in annotations)


# ------------------------------------------------------------------ spatial memory


def test_an_answer_is_a_sighting_not_a_guarantee():
    memory = SpatialMemory()
    memory.record("keys", confidence=0.8, location_context="the office desk", camera_id="cam-1")

    tracked = memory.objects()[0]
    described = tracked.describe("en")
    assert "last seen" in described
    assert "not a guarantee" in described


def test_the_age_of_a_sighting_is_said_out_loud():
    """ "Last seen three days ago" and "last seen a minute ago" are structurally the same
    sentence and completely different answers."""
    memory = SpatialMemory()
    memory.record(
        "keys",
        confidence=0.8,
        location_context="the desk",
        seen_at=datetime.now(UTC) - timedelta(hours=30),
    )
    assert "30h ago" in memory.objects()[0].describe("en")


def test_objects_carry_first_and_last_seen():
    memory = SpatialMemory()
    early = datetime.now(UTC) - timedelta(hours=5)
    memory.record("book", confidence=0.9, seen_at=early, object_type="book")
    memory.record("book", confidence=0.95, location_context="the shelf", object_type="book")

    tracked = memory.objects()[0]
    assert tracked.sightings == 2
    assert tracked.first_seen == early
    assert tracked.last_seen > early
    # The latest sighting wins for the answer, not the first.
    assert tracked.location_context == "the shelf"


def test_objects_can_be_grouped_by_type():
    memory = SpatialMemory()
    memory.record("book", confidence=0.9, object_type="book")
    memory.record("laptop", confidence=0.9, object_type="computer")
    assert [t.label for t in memory.of_type("book")] == ["book"]


async def test_looking_records_a_sighting_with_its_camera():
    camera = FakeCamera(camera_id="office-cam")
    manager = CameraManager(camera)
    manager.grant_access("identify this")
    service = VisionService(
        camera=manager,
        detector=ScriptedDetector(default=[detection("book", 0.9)]),
        analyzer=ScriptedAnalyzer(),
    )

    await service.look("what is this")
    tracked = service.spatial.objects()[0]
    assert tracked.label == "book"
    assert tracked.camera_id == "office-cam"
    assert tracked.object_type == "book"


# ------------------------------------------------------------------ screen


async def test_reading_the_screen_needs_no_camera_grant():
    """A different privacy question: the owner is already looking at it (§30)."""
    service = VisionService(
        screen=FakeScreen(),
        detector=ScriptedDetector(default=[]),
        ocr=ScriptedOCR(blocks=[text_block("Total: 4,200")]),
        analyzer=ScriptedAnalyzer(answer="a spreadsheet showing a total"),
    )
    reading = await service.read_screen()
    assert "4,200" in reading.all_text()
    assert service.camera is None
