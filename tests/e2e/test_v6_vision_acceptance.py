"""V6 acceptance test — "Thursday นี่คืออะไร".

The owner holds a book up to the camera and asks what it is. The pipeline detects the
object, reads the cover, scans the barcode, and answers with all three — saying which part
came from where, so a *read* title is distinguishable from a *recognised* shape.

The first test in this file is not that flow. It is the one that matters more: the same
question, with no camera grant, and nothing switches on.
"""

from __future__ import annotations

import pytest
from thursday_shared.ids import new_id
from thursday_shared.models import UserRequest
from thursday_vision.camera import CameraManager, CameraState
from thursday_vision.fake import (
    FakeCamera,
    FakeScreen,
    ScriptedBarcodes,
    ScriptedDetector,
    ScriptedOCR,
    detection,
    text_block,
)
from thursday_vision.ports import Barcode
from thursday_vision.service import VisionService


@pytest.fixture
def seeing(container):
    """A camera pointed at a book, with the pipeline wired but no grant given."""
    camera = FakeCamera(camera_id="desk-cam")
    manager = CameraManager(camera)
    container.camera = manager
    container.vision = VisionService(
        camera=manager,
        screen=FakeScreen(),
        detector=ScriptedDetector(default=[detection("book", 0.93)]),
        ocr=ScriptedOCR(
            blocks=[
                text_block("Clean Architecture", 0.91, area=0.35),
                text_block("Robert C. Martin", 0.86, area=0.08),
            ]
        ),
        barcodes=ScriptedBarcodes(codes=[Barcode(value="9780134494166", kind="EAN13")]),
        analyzer=container.vision._analyzer,
        spatial=container.spatial,
        bus=container.bus,
    )
    return camera, manager


async def test_asking_what_this_is_does_not_switch_the_camera_on(container, seeing, session_id):
    """The question alone is not consent. Thursday asks; it does not open the camera and
    apologise afterwards."""
    camera, manager = seeing

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday นี่คืออะไร")
    )

    assert camera.opens == 0
    assert manager.state is CameraState.OFF
    assert manager.indicator_on is False
    # And the owner is told why, rather than getting silence or a failure.
    assert "กล้อง" in response.text


async def test_with_permission_thursday_identifies_the_book(container, seeing, session_id):
    camera, manager = seeing
    manager.grant_access("the owner asked what they are holding", max_captures=1)

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday นี่คืออะไร")
    )

    # Detected, read and scanned — each contributing its own part of the answer.
    assert "book" in response.text
    assert "Clean Architecture" in response.text
    assert "9780134494166" in response.text

    # One look, and the camera closed itself behind it.
    assert camera.captures == 1
    assert manager.state is CameraState.OFF
    assert manager.indicator_on is False


async def test_the_sighting_is_remembered_as_a_sighting(container, seeing, session_id):
    _, manager = seeing
    manager.grant_access("identify this", max_captures=1)
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday นี่คืออะไร")
    )

    later = await container.engine.handle_request(
        UserRequest(conversation_id=new_id(), text="Thursday หนังสืออยู่ไหน")
    )
    # Phrased as a last sighting, never as a claim about where it is now (§25).
    assert "ยังไม่ยืนยัน" in later.text


async def test_asking_where_something_was_never_seen_says_so(container, seeing, session_id):
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ผมวางกุญแจไว้ที่ไหน")
    )
    assert "ไม่เคยเห็น" in response.text


async def test_an_unrecognisable_frame_asks_rather_than_guessing(container, seeing, session_id):
    _, manager = seeing
    container.vision._detector = ScriptedDetector(default=[])
    container.vision._ocr = ScriptedOCR(blocks=[])
    container.vision._barcodes = ScriptedBarcodes(codes=[])
    manager.grant_access("identify this", max_captures=1)

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday นี่คืออะไร")
    )
    assert "มองไม่ออก" in response.text


async def test_a_screen_question_needs_no_camera(container, seeing, session_id):
    """The owner is already looking at the screen; reading it is not the same act as
    switching on a camera in the room (§30)."""
    camera, manager = seeing

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ตรงนี้ผิดอะไร")
    )

    assert camera.opens == 0
    assert manager.indicator_on is False
    assert response.text


async def test_no_frame_ever_reaches_the_event_bus(container, seeing, session_id):
    """What Thursday saw is a fact about the owner's home; the picture of it does not go
    on a bus that fans out to subscribers."""
    _, manager = seeing
    manager.grant_access("identify this", max_captures=1)
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday นี่คืออะไร")
    )

    for event in container.bus.history("vision.*"):
        serialised = str(event.payload)
        assert "PNG" not in serialised
        assert "data" not in event.payload
        # Labels and counts only.
        assert set(event.payload) <= {"objects", "text_blocks", "barcodes", "uncertain"}


async def test_never_seen_falls_back_to_what_the_owner_said(container, seeing, session_id):
    """Answering "I have never seen it" while holding a note that says where it lives
    would be absurd. Sightings first, then memory."""
    from thursday_shared.enums import MemoryLayer, MemorySource
    from thursday_shared.models import MemoryWrite

    await container.memory.write(
        MemoryWrite(
            layer=MemoryLayer.SEMANTIC,
            content="กุญแจสำรองอยู่ในลิ้นชักบนสุด",
            source=MemorySource.USER,
            importance=0.8,
        ),
        force=True,
    )

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="Thursday ผมวางกุญแจไว้ที่ไหน")
    )
    assert "ลิ้นชัก" in response.text


async def test_asking_what_thursday_remembers_is_not_a_sighting_question(
    container, seeing, session_id
):
    """ "จำได้ไหมว่า…อยู่ไหน" asks what Thursday remembers, not what a camera saw. The
    framing wins over the location words inside it."""
    from thursday_core import intent_rules
    from thursday_shared.enums import IntentKind

    match = intent_rules.parse("จำได้ไหมว่าห้องทำงานผมอยู่ไหน")
    assert match is not None
    assert match.intent.kind is IntentKind.MEMORY_RECALL
