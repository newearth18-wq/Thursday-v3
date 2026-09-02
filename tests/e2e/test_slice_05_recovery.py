"""Slice 5 — what happens when things go wrong.

Every earlier slice assumes the machine answers, the agent finishes, and the budget holds.
This one removes each of those in turn. The property under test is always the same, and it
is the property the whole design rests on: **Thursday's account of what happened matches
what happened.**

A system that is right when everything works and optimistic when it doesn't is worse than
one that is simply unreliable, because the owner cannot tell the two states apart.
"""

from __future__ import annotations

from thursday_shared.enums import TaskState, VoiceMode
from thursday_shared.ids import new_id
from thursday_shared.models import DeviceAction, UserRequest

from tests.helpers import connect_failing_node


async def test_a_machine_that_goes_quiet_mid_task_is_reported(container, tmp_path, session_id):
    """The node accepts one action and then stops answering. The owner hears about it."""
    from thursday_devices.fake import FakeDeviceNode

    # One action is all this machine has left in it: the turn below is the one that finds
    # it gone, which is the case worth asserting.
    node = FakeDeviceNode(name="Flaky-PC", allowed_roots=[tmp_path], offline_after=1)
    session = node.session()
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Flaky-PC")

    first = await node.executor.execute(DeviceAction(action="app.open", args={"app": "chrome"}))
    assert first.ok

    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday open notepad",
            device_id=session.device_id,
        )
    )
    assert response.voice_mode is not VoiceMode.SUCCESS
    assert response.verified is False or "offline" in (response.text + str(response.detail)).lower()


async def test_a_failed_task_is_not_retried_forever(container, tmp_path, session_id):
    """PART 62 — the same failure repeated is not new information, and a loop the owner
    cannot see is the worst way to spend their afternoon."""
    session = await connect_failing_node(container, tmp_path)
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id, text="Thursday open chrome", device_id=session.device_id
        )
    )

    attempts = [e for e in container.audit.entries() if e.tool == "app.open"]
    assert 1 <= len(attempts) <= 4, f"{len(attempts)} attempts is not a bounded retry"
    assert container.tasks.list()[0].status is TaskState.FAILED


async def test_the_audit_trail_survives_a_failure(container, tmp_path, session_id):
    """A run that went wrong is exactly the run someone will want to reconstruct."""
    session = await connect_failing_node(container, tmp_path)
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id, text="Thursday open chrome", device_id=session.device_id
        )
    )

    entries = container.audit.entries()
    assert entries
    assert container.audit.verify_chain(), "the chain broke on the failure path"
    # The unverified attempt is recorded as such, not as a success and not as nothing.
    assert any(entry.result in {"unverified", "failed"} for entry in entries)


async def test_nothing_that_did_not_happen_is_offered_as_undoable(container, tmp_path, session_id):
    """§40. An undo list that includes actions which never took effect would have the owner
    reversing things that were never done."""
    session = await connect_failing_node(container, tmp_path)
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id, text="Thursday open chrome", device_id=session.device_id
        )
    )
    assert not any(u.operation == "app.close" for u in container.undo.pending())


async def test_the_owner_can_stop_everything_without_the_model(container, office_pc, session_id):
    """§69/PART 98 — the stop path does not go through reasoning, because the reason to
    reach for it is often that reasoning is what went wrong."""
    await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            text="Thursday เปิด chrome",
            device_id=office_pc.device_id,
        )
    )

    await container.emergency_stop("all")

    # Nothing is granted, and the next attempt is refused rather than queued.
    assert container.permissions.list_grants() == []
    blocked = await container.engine.handle_request(
        UserRequest(
            conversation_id=new_id(),
            text="Thursday open notepad",
            device_id=office_pc.device_id,
        )
    )
    assert blocked.voice_mode is not VoiceMode.SUCCESS

    container.permissions.set_lockdown(False)
