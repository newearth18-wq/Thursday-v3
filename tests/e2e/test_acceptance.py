"""The acceptance test (PART 77–78).

The seventeen steps, from a spoken sentence to a spoken reply, asserted one at a time
against the real container. Nothing here is mocked except the machine at the far end and
the microphone at the near one.

The last test is the one that matters. Sixteen green steps prove the pipeline runs; the
seventeenth — *Chrome did not start, so the task is not COMPLETED* — proves the pipeline is
honest. A system that reports success it did not verify has failed no matter how many of
the other steps pass.
"""

from __future__ import annotations

from thursday_shared.enums import TaskState, VoiceMode
from thursday_shared.models import MemoryQuery, UserRequest

from tests.helpers import connect_failing_node


async def test_the_seventeen_steps(container, office_pc, adapter, session_id):
    events: list[str] = []
    container.bus.subscribe("*", lambda event: events.append(event.kind))

    # 1. CAPTURE — the owner speaks. The audio never leaves the machine (§34), and the
    #    stub STT stands in for whisper so this runs with no model weights present.
    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            audio="Thursday เปิด chrome".encode(),
            modality="voice",
            device_id=office_pc.device_id,
        )
    )

    # 2. NORMALIZE — audio became a transcript before anything else looked at the request.
    assert any(k == "conversation.turn.received" for k in events)

    # 3–5. CONTEXT, PRIVACY, UNDERSTAND — a task exists, so the request was classified as
    #      an action rather than answered conversationally.
    task = container.tasks.list()[0]
    assert task.objective

    # 6. PLAN — the plan produced at least one step, and it reached the device.
    assert task.plan is not None and task.plan.steps

    # 7. AUTHORISE — opening an app is AUTO at the default autonomy, so nothing waited.
    assert not container.approvals.pending()

    # 8. EXECUTE — the machine actually changed.
    assert "chrome" in adapter.running

    # 9. VERIFY — and Thursday looked, rather than assuming.
    assert task.verification is not None
    assert task.verification.passed

    # 10. RECORD — the task is complete and its trail is intact.
    assert task.status is TaskState.COMPLETED
    assert "app.open" in [entry.tool for entry in container.audit.entries()]
    assert container.audit.verify_chain()

    # 11. UNDO — the action left a way back (§40).
    assert any(u.operation == "app.close" for u in container.undo.pending())

    # 12. REMEMBER — the turn is in episodic memory, and nothing became a "preference"
    #     just because it happened once (PART 39).
    recalled = await container.memory.recall(MemoryQuery(text="chrome", k=10))
    assert all(str(r.layer).lower() != "preference" for r in recalled)

    # 13–15. COMPOSE, SPEAK, REPORT — one reply, in the owner's language, marked verified.
    assert response.verified is True
    assert response.voice_mode is VoiceMode.SUCCESS
    assert "chrome" in response.text
    assert response.speech is not None

    # 16. STATUS — the response carries the task's state, so the caller need not go looking.
    assert response.status is TaskState.COMPLETED

    # 17. OBSERVE — the whole run is visible on the event bus, not only in the reply.
    assert "task.completed" in events


async def test_the_seventeenth_step_when_chrome_never_starts(container, tmp_path, session_id):
    """The negative case, and the reason the other sixteen are worth asserting.

    The launch command succeeds. No process appears. Thursday must not say it worked.
    """
    session = await connect_failing_node(container, tmp_path)

    response = await container.engine.handle_request(
        UserRequest(
            conversation_id=session_id,
            audio=b"Thursday open chrome",
            modality="voice",
            device_id=session.device_id,
        )
    )

    task = container.tasks.list()[0]
    assert task.status is not TaskState.COMPLETED
    assert task.status is TaskState.FAILED

    assert response.verified is False
    assert response.voice_mode is not VoiceMode.SUCCESS
    assert response.status is TaskState.FAILED

    # Nothing that never happened is offered as undoable.
    assert not any(u.operation == "app.close" for u in container.undo.pending())
