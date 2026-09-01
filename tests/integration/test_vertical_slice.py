"""The first vertical slice, end to end (§15, §89).

    owner speaks → Thursday understands → commands the PC → verifies → replies

Plus the case that matters more than the happy path: when verification fails, Thursday must
not say it succeeded (§76).
"""

from __future__ import annotations

import pytest
from thursday_shared.enums import PolicyDecision, TaskState, VoiceMode
from thursday_shared.models import ActionRequest


async def test_open_app_is_verified_before_success_is_reported(
    container, office_pc, adapter, session_id
):
    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday เปิด chrome", device_id=office_pc.device_id
    )

    # The device actually changed state, and the reply says so.
    assert "chrome" in adapter.running
    assert reply.verified is True
    assert reply.voice_mode is VoiceMode.SUCCESS
    assert "chrome" in reply.text

    task = container.tasks.list()[0]
    assert task.status is TaskState.COMPLETED
    assert task.verification is not None and task.verification.passed

    # An audit row exists for the action, and the chain is intact.
    tools_used = [e.tool for e in container.audit.entries()]
    assert "open_app" in tools_used
    assert container.audit.verify_chain()

    # The action left an undo path behind (§40).
    assert any(u.operation == "close_app" for u in container.undo.pending())


async def test_dispatch_without_observable_effect_is_not_success(container, tmp_path, session_id):
    """The command is accepted, the process never appears — Thursday must not claim success."""
    from tests.helpers import connect_failing_node

    session = await connect_failing_node(container, tmp_path)

    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday open chrome", device_id=session.device_id
    )

    assert reply.verified is False
    assert reply.voice_mode is VoiceMode.WARNING
    assert "สำเร็จ" not in reply.text
    assert container.tasks.list()[0].status is TaskState.FAILED


async def test_risky_request_is_never_executed_silently(container, office_pc, adapter, session_id):
    """A shell command is ASK by policy; an unclassifiable one becomes a question.

    Either way the invariant is the same: nothing ran.
    """
    verdict = container.permissions.decide(ActionRequest(action="run_shell", resource="rm -rf /"))
    assert verdict.decision is PolicyDecision.ASK

    await container.engine.handle_turn(
        session_id=session_id,
        text="Thursday run shell command rm -rf /tmp/x",
        device_id=office_pc.device_id,
    )
    assert not any(e.tool == "run_shell" and e.result == "ok" for e in container.audit.entries())


async def test_approval_gates_a_risky_tool_call(container, office_pc, tmp_path, session_id):
    """The full ASK path: pending approval → decision → execution (or refusal)."""
    import asyncio

    from thursday_shared.enums import ApprovalScope
    from thursday_shared.errors import PermissionDenied
    from thursday_shared.models import ToolCall

    target = tmp_path / "scratch.txt"
    target.write_text("delete me", encoding="utf-8")
    call = ToolCall(
        tool="delete",
        args={"path": str(target)},
        device_id=office_pc.device_id,
        reason="tidy up the scratch file",
    )

    running = asyncio.create_task(container.executor.execute(call, agent="computer"))
    await asyncio.sleep(0.05)

    pending = container.approvals.pending()
    assert len(pending) == 1
    approval = pending[0]
    # The request carries everything the owner needs in order to decide (§38).
    assert approval.action == "delete"
    assert approval.resource == str(target)
    assert approval.expected_outcome
    assert approval.consequence_of_refusal

    await container.approvals.decide(approval.id, approve=True, scope=ApprovalScope.ONCE)
    result = await running
    assert result.ok and result.verified
    assert not target.exists()
    assert result.undo is not None

    # A rejection stops the next one, and grants nothing standing.
    other = tmp_path / "keep.txt"
    other.write_text("keep me", encoding="utf-8")
    second = asyncio.create_task(
        container.executor.execute(
            ToolCall(tool="delete", args={"path": str(other)}, device_id=office_pc.device_id),
            agent="computer",
        )
    )
    await asyncio.sleep(0.05)
    await container.approvals.decide(container.approvals.pending()[0].id, approve=False)
    with pytest.raises(PermissionDenied):
        await second
    assert other.exists()


async def test_stop_cancels_running_work(container, office_pc, session_id):
    await container.tasks.create(title="long job", objective="long job")
    task = container.tasks.list()[0]
    await container.tasks.transition(task.id, TaskState.PLANNING)
    await container.tasks.transition(task.id, TaskState.RUNNING)

    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday หยุด", device_id=office_pc.device_id
    )
    assert "หยุด" in reply.text
    assert container.tasks.get(task.id).status is TaskState.CANCELLED


async def test_device_status_question_answered_from_the_registry(container, office_pc, session_id):
    reply = await container.engine.handle_turn(
        session_id=session_id, text="Office-PC ยังออนไลน์ไหม", device_id=office_pc.device_id
    )
    assert "Office-PC" in reply.text
    assert "ออนไลน์" in reply.text or "online" in reply.text


async def test_episodic_memory_is_written_after_a_verified_task(container, office_pc, session_id):
    before = container.memory.stats().get("episodic", 0)
    await container.engine.handle_turn(
        session_id=session_id, text="Thursday เปิด chrome", device_id=office_pc.device_id
    )
    assert container.memory.stats().get("episodic", 0) > before


async def test_emergency_stop_locks_the_system_down(container, office_pc):
    actions = await container.emergency_stop("all")
    assert actions["devices_disconnected"] >= 1
    assert container.permissions.lockdown is True
    verdict = container.permissions.decide(ActionRequest(action="open_app", resource="chrome"))
    assert verdict.decision is PolicyDecision.BLOCK
