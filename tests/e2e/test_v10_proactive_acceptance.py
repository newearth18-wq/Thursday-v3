"""V10 acceptance test — Thursday speaks first.

    A meeting tomorrow. No preparation document.

    Thursday:  "พรุ่งนี้มีประชุมและยังไม่พบเอกสารเตรียมประชุม ต้องการให้ผมจัดเตรียมให้ไหม"
    Owner:     "ทำเลย"
    Thursday:  creates a task → delegates agents → Supervisor checks → returns the result

Everything in this file is arranged around one sentence from the spec: *"ห้ามกลายเป็น
autonomous system ที่ทำอะไรก็ได้เอง"*. So the tests come in pairs — one that the proactive
half works, one that it stops where it should. The offer must be made; the document must
*not* be written until somebody says yes.

"ทำเลย" was already parsed as an approval before V10 and nothing handled it, so saying it
did nothing at all. That is fixed here, and the precedence it needed — an approval that is
waiting outranks a suggestion nobody asked for — is asserted below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_agents.ports import CalendarEvent
from thursday_automation.proactive import upcoming_meetings
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import ProactivityLevel, TaskState
from thursday_shared.ids import new_id
from thursday_shared.models import UserRequest

from tests.conftest import FakeAdapter


@pytest.fixture
async def tomorrow_has_a_meeting(container, tmp_path):
    """A machine, a meeting in the calendar, and nothing prepared for it."""
    device_id = new_id()
    node = LoopbackDeviceSession(
        device_id=device_id,
        name="Office-PC",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await container.hub.register(node, location_context="office")
    container.world.update(active_device_id=device_id, active_device_name="Office-PC")

    starts = datetime.now(UTC) + timedelta(hours=20)
    event = await container.calendar.create(
        CalendarEvent(
            title="ประชุมทีม",
            start=starts,
            end=starts + timedelta(hours=1),
            attendees=("somchai@example.com",),
        )
    )
    container.automations.gate.level = ProactivityLevel.HIGH
    container.proactive.observe(
        "meetings",
        upcoming_meetings(container.calendar, has_preparation=lambda e: False),
    )
    return node, event


async def raise_offers(container) -> list:
    """Run a sweep and turn what it found into offers, as the worker loop would."""
    observations = await container.proactive.sweep()
    return [
        container.offers.make(o.describe("th"), action=o.action, fingerprint=o.fingerprint)
        for o in observations
    ]


# ------------------------------------------------------------------ Thursday speaks first


async def test_thursday_notices_the_unprepared_meeting(container, tomorrow_has_a_meeting):
    observations = await container.proactive.sweep()
    assert len(observations) == 1
    said = observations[0].describe("th")
    assert "ประชุม" in said
    assert "ยังไม่พบเอกสารเตรียมประชุม" in said
    assert "ไหมครับ" in said  # a question, not an announcement of something already done


async def test_it_offers_rather_than_acting(container, tomorrow_has_a_meeting):
    """Writing a document is reversible and local and still not something to do unasked.
    A file the owner did not ask for is a file they did not expect."""
    observations = await container.proactive.sweep()
    assert not observations[0].may_act_alone


async def test_nothing_happens_until_the_owner_says_so(container, tomorrow_has_a_meeting):
    """The half of the acceptance test that is easy to forget to check."""
    await raise_offers(container)
    assert container.tasks.list() == []


async def test_saying_yes_does_the_work(container, session_id, tomorrow_has_a_meeting):
    await raise_offers(container)

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )

    assert response.task_id is not None
    task = container.tasks.get(response.task_id)
    assert task.status is TaskState.COMPLETED
    assert response.verified is True


async def test_the_work_goes_through_agents_and_the_supervisor(
    container, session_id, tomorrow_has_a_meeting
):
    """A proactive request that took a shortcut past the planner, the permission engine or
    the Supervisor would be a second execution path — and the one thing V10 must not add is
    a way for Thursday to act on its own initiative *and* on its own terms."""
    await raise_offers(container)
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )

    task = container.tasks.get(response.task_id)
    assert [step.name for step in task.plan.steps] == ["research", "document"]
    assert all(step.status is TaskState.COMPLETED for step in task.plan.steps)
    assert task.verification is not None and task.verification.passed


async def test_the_prepared_document_is_about_the_meeting(
    container, session_id, tomorrow_has_a_meeting
):
    """Not merely that a document exists. An offline model returning "I cannot answer
    analytical questions right now" once passed as a meeting note."""
    await raise_offers(container)
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )

    task = container.tasks.get(response.task_id)
    document = next(s.output["document"] for s in task.plan.steps if s.name == "document")
    assert "ประชุมทีม" in document


async def test_the_reply_says_it_came_from_an_offer(container, session_id, tomorrow_has_a_meeting):
    """ "Thursday did this because it offered and I said yes" is a different fact from "I
    asked for this", and the owner should be able to tell them apart later."""
    offers = await raise_offers(container)
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )
    assert offers[0].text in (response.detail or "")


# ------------------------------------------------------------------ and where it stops


async def test_saying_no_leaves_it_alone(container, session_id, tomorrow_has_a_meeting):
    await raise_offers(container)

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ไม่ต้อง")
    )
    assert container.tasks.list() == []
    assert "ไม่ทำ" in response.text


async def test_yes_with_nothing_outstanding_does_nothing(container, session_id):
    """The word on its own is not an instruction."""
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )
    assert container.tasks.list() == []
    assert "ยังไม่ได้ถาม" in response.text


async def test_an_approval_outranks_an_offer(container, session_id, tomorrow_has_a_meeting):
    """Both are answered with the same word and they are not the same thing. Someone
    answering has almost certainly just been interrupted by the approval."""
    from thursday_shared.models import ApprovalRequest

    await raise_offers(container)
    await container.approvals.request(ApprovalRequest(action="file.delete", resource="/tmp/x"))

    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="ทำเลย")
    )

    assert "file.delete" in response.text
    # The offer is untouched: one answer settles one question.
    assert len(container.offers.pending()) == 1


async def test_the_same_meeting_is_not_raised_twice(container, tomorrow_has_a_meeting):
    """A meeting tomorrow is still true in five minutes. Saying so again is nagging."""
    assert len(await container.proactive.sweep()) == 1
    assert await container.proactive.sweep() == []


async def test_nothing_is_raised_when_proactivity_is_off(container, tomorrow_has_a_meeting):
    container.automations.gate.level = ProactivityLevel.OFF
    assert await container.proactive.sweep() == []


async def test_a_meeting_with_preparation_is_not_mentioned(container, tmp_path):
    """The observation is about the *absence* of a document, not about the meeting."""
    starts = datetime.now(UTC) + timedelta(hours=20)
    await container.calendar.create(CalendarEvent(title="ประชุมทีม", start=starts))
    container.automations.gate.level = ProactivityLevel.HIGH
    container.proactive.observe(
        "meetings",
        upcoming_meetings(container.calendar, has_preparation=lambda e: True),
    )
    assert await container.proactive.sweep() == []
