"""V8 acceptance test — one Thursday, several machines.

    On the phone: "Thursday คอมที่บ้านเปิดอยู่ไหม"
                  → Thursday queries the Home-PC node and reports its status
    Then:         "เปิด Chrome ให้หน่อย"
                  → Thursday routes the task to the Home-PC

The second sentence is the whole test. It names no machine. Every earlier version of this
system would have opened Chrome on the phone in the owner's hand — not a failure, which
would at least be visible, but a success on the wrong machine, reported as success, in
another building.

Three things have to hold at once for that not to happen, and each has its own failure mode:
the conversation has to remember which machine it is about, the reply has to *say* which
machine it used, and the phone has to be trusted to drive the PC in the first place.
"""

from __future__ import annotations

import pytest
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import TaskState, TrustLevel
from thursday_shared.errors import ApprovalRequired
from thursday_shared.ids import new_id
from thursday_shared.models import PermissionSet, ToolCall, UserRequest

from tests.conftest import FakeAdapter


@pytest.fixture
async def home_and_phone(container, tmp_path):
    """A PC at home, and the phone the owner is holding somewhere else."""
    home_id, phone_id = new_id(), new_id()

    home = LoopbackDeviceSession(
        device_id=home_id,
        name="Home-PC",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await container.hub.register(home, location_context="home")

    phone = LoopbackDeviceSession(
        device_id=phone_id,
        name="Pixel",
        kind="phone",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await container.hub.register(phone, location_context="anywhere")

    # The owner vouches for their own phone. Without this the flow below is refused, and
    # `test_an_unvouched_phone_cannot_drive_the_pc` asserts exactly that.
    container.hub.set_trust(phone_id, TrustLevel.TRUSTED)
    container.world.update(active_device_id=phone_id, active_device_name="Pixel")
    return home, phone


# ------------------------------------------------------------------ the acceptance flow


async def test_asking_after_a_machine_by_name_reports_its_status(
    container, session_id, home_and_phone
):
    _, phone = home_and_phone
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    assert "Home-PC" in response.text


async def test_the_follow_up_lands_on_the_machine_that_was_asked_about(
    container, session_id, home_and_phone
):
    """The sentence names no device. The conversation does."""
    home, phone = home_and_phone

    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="เปิด Chrome ให้หน่อย", device_id=phone.device_id)
    )

    assert "chrome" in home.executor.adapter.running
    assert phone.executor.adapter.running == {}
    task = container.tasks.list()[0]
    assert task.status is TaskState.COMPLETED
    assert response.verified is True


async def test_the_reply_says_which_machine_it_used(container, session_id, home_and_phone):
    """Routing somewhere the owner did not name is only safe if Thursday says so.

    Without this the feature is indistinguishable from a bug: the owner asks for Chrome,
    Chrome does not appear in front of them, and nothing explains why.
    """
    _, phone = home_and_phone
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="เปิด Chrome ให้หน่อย", device_id=phone.device_id)
    )
    assert "Home-PC" in response.text


async def test_the_app_name_is_the_app_not_the_politeness(container, session_id, home_and_phone):
    """ "เปิด Chrome ให้หน่อย" asks for Chrome."""
    home, phone = home_and_phone
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="เปิด Chrome ให้หน่อย", device_id=phone.device_id)
    )
    assert list(home.executor.adapter.running) == ["chrome"]


async def test_saying_this_machine_brings_it_back(container, session_id, home_and_phone):
    """The owner must be able to end the digression by saying so."""
    home, phone = home_and_phone
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="เปิด notepad บนเครื่องนี้",
            device_id=phone.device_id,
        )
    )
    assert "notepad" in phone.executor.adapter.running
    assert "notepad" not in home.executor.adapter.running


# ------------------------------------------------------------------ security


async def test_an_unvouched_phone_cannot_drive_the_pc(container, session_id, tmp_path):
    """The same flow, with the phone left at its enrolment trust. Nothing happens."""
    home_id, phone_id = new_id(), new_id()
    home = LoopbackDeviceSession(
        device_id=home_id,
        name="Home-PC",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    phone = LoopbackDeviceSession(
        device_id=phone_id,
        name="Pixel",
        kind="phone",
        executor=NodeExecutor(FakeAdapter(), allowed_roots=[tmp_path]),
    )
    await container.hub.register(home, location_context="home")
    await container.hub.register(phone)
    container.world.update(active_device_id=phone_id, active_device_name="Pixel")

    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone_id,
        )
    )
    response = await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="เปิด Chrome ให้หน่อย", device_id=phone_id)
    )

    assert home.executor.adapter.running == {}
    assert phone.executor.adapter.running == {}
    assert "not trusted" in response.text
    # And it is refused, not attempted-and-failed: nothing ran on the target machine.
    assert container.tasks.list()[0].status is TaskState.FAILED


async def test_the_same_write_is_automatic_locally_and_asked_remotely(
    container, tmp_path, home_and_phone
):
    """One call, two origins. Distance is the only difference, and it is the whole point."""
    home, phone = home_and_phone
    target = tmp_path / "notes.txt"

    def write_from(origin_id):
        return ToolCall(
            tool="file.write",
            args={"path": str(target), "content": "hello"},
            device_id=home.device_id,
            origin_device_id=origin_id,
        )

    local = await container.executor.execute(
        write_from(home.device_id),
        permissions=PermissionSet(max_level=4),
        wait_for_approval=False,
    )
    assert local.ok

    with pytest.raises(ApprovalRequired):
        await container.executor.execute(
            write_from(phone.device_id),
            permissions=PermissionSet(max_level=4),
            wait_for_approval=False,
        )


async def test_the_audit_records_both_ends_of_a_remote_command(
    container, session_id, home_and_phone
):
    """ "Who told my PC to do that, and from where" has to be answerable afterwards."""
    home, phone = home_and_phone
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday คอมที่บ้านเปิดอยู่ไหม",
            device_id=phone.device_id,
        )
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=session_id, text="เปิด Chrome ให้หน่อย", device_id=phone.device_id)
    )

    opens = [e for e in container.audit.entries(limit=50) if e.tool == "app.open"]
    assert opens, "the action was not audited at all"
    assert all(e.device_id == home.device_id for e in opens)
    assert all(e.origin_device_id == phone.device_id for e in opens)


# ------------------------------------------------------------------ handoff


async def test_a_task_run_on_one_machine_is_answerable_from_another(
    container, tmp_path, home_and_phone
):
    """The owner starts something at the PC, walks off, and asks their phone about it.

    A new device and a new conversation — so this only works because tasks live in the
    core rather than in the session that started them.
    """
    home, phone = home_and_phone
    at_the_pc = new_id()
    await container.engine.handle_request(
        UserRequest(
            conversation_id=at_the_pc, text="Thursday เปิด notepad", device_id=home.device_id
        )
    )

    on_the_phone = new_id()
    response = await container.engine.handle_request(
        UserRequest(conversation_id=on_the_phone, text="ผลเมื่อกี้เป็นยังไง", device_id=phone.device_id)
    )

    assert "notepad" in response.text
    assert "Home-PC" in response.text  # which machine, not just what happened


async def test_continuing_from_the_previous_machine_routes_there(
    container, tmp_path, home_and_phone
):
    home, phone = home_and_phone
    at_the_pc = new_id()
    await container.engine.handle_request(
        UserRequest(
            conversation_id=at_the_pc, text="Thursday เปิด notepad", device_id=home.device_id
        )
    )

    on_the_phone = new_id()
    await container.engine.handle_request(
        UserRequest(
            conversation_id=on_the_phone,
            text="ทำต่อจากเครื่องเมื่อกี้",
            device_id=phone.device_id,
        )
    )
    await container.engine.handle_request(
        UserRequest(conversation_id=on_the_phone, text="เปิด chrome", device_id=phone.device_id)
    )

    assert "chrome" in home.executor.adapter.running
    assert phone.executor.adapter.running == {}
