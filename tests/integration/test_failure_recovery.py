"""Failure paths (§60, §96) — the behaviour that separates an assistant from a demo."""

from __future__ import annotations

import asyncio

import pytest
from thursday_devices.hub import LoopbackDeviceSession
from thursday_devices.node.executor import NodeExecutor
from thursday_shared.enums import ApprovalScope, TaskState
from thursday_shared.errors import PermissionDenied
from thursday_shared.ids import new_id
from thursday_shared.models import ToolCall

from tests.conftest import FakeAdapter


async def test_retries_are_bounded_and_do_not_loop(container, tmp_path, session_id, monkeypatch):
    """A repeatedly failing step must stop, not spin (§96)."""
    adapter = FakeAdapter()
    attempts = {"count": 0}
    original = adapter.find_processes

    async def flaky(name: str):
        attempts["count"] += 1
        return []  # the process never appears, so verification never passes

    adapter.find_processes = flaky  # type: ignore[method-assign]
    session = LoopbackDeviceSession(
        device_id=new_id(),
        name="Flaky-PC",
        executor=NodeExecutor(adapter, allowed_roots=[tmp_path]),
    )
    await container.hub.register(session)
    container.world.update(active_device_id=session.device_id, active_device_name="Flaky-PC")

    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday open chrome", device_id=session.device_id
    )
    assert reply.verified is False
    # Bounded: the ladder stopped instead of retrying forever.
    assert attempts["count"] < 20
    assert container.tasks.list()[0].status is TaskState.FAILED
    adapter.find_processes = original  # type: ignore[method-assign]


async def test_a_disconnected_device_mid_task_is_reported_not_swallowed(
    container, office_pc, session_id
):
    await container.hub.unregister(office_pc.device_id)
    container.world.update(active_device_id=None, active_device_name=None)

    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday open chrome", device_id=office_pc.device_id
    )
    assert reply.verified is False
    assert reply.text  # Thursday says something rather than failing silently


async def test_a_rejected_approval_does_not_leave_a_standing_grant(container, office_pc, tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep", encoding="utf-8")

    call = ToolCall(tool="delete", args={"path": str(target)}, device_id=office_pc.device_id)
    running = asyncio.create_task(container.executor.execute(call, agent="computer"))
    await asyncio.sleep(0.05)
    await container.approvals.decide(container.approvals.pending()[0].id, approve=False)

    with pytest.raises(PermissionDenied):
        await running
    assert target.exists()
    assert container.permissions.list_grants() == []


async def test_always_allow_is_scoped_to_the_directory_not_the_filesystem(
    container, office_pc, tmp_path
):
    """'Always allow' must not become 'allow everywhere' (§8.4, T5)."""
    inside = tmp_path / "scratch" / "a.txt"
    inside.parent.mkdir(parents=True)
    inside.write_text("x", encoding="utf-8")

    call = ToolCall(tool="delete", args={"path": str(inside)}, device_id=office_pc.device_id)
    running = asyncio.create_task(container.executor.execute(call, agent="computer"))
    await asyncio.sleep(0.05)
    await container.approvals.decide(
        container.approvals.pending()[0].id, approve=True, scope=ApprovalScope.ALWAYS
    )
    assert (await running).ok

    grants = container.permissions.list_grants()
    assert len(grants) == 1
    assert grants[0].resource_glob.endswith("scratch/*")
    assert grants[0].expires_at is not None

    # A file in a different directory is still gated.
    from thursday_shared.enums import PolicyDecision
    from thursday_shared.models import ActionRequest

    verdict = container.permissions.decide(
        ActionRequest(action="delete", resource=str(tmp_path / "elsewhere" / "b.txt"))
    )
    assert verdict.decision is PolicyDecision.ASK


async def test_an_expired_approval_fails_closed(container, office_pc, tmp_path):
    """Silence is not consent: a timed-out approval blocks the action."""
    target = tmp_path / "timeout.txt"
    target.write_text("x", encoding="utf-8")

    call = ToolCall(tool="delete", args={"path": str(target)}, device_id=office_pc.device_id)
    with pytest.raises(PermissionDenied, match="expired"):
        # The container fixture sets a 2-second approval TTL; nobody answers.
        await container.executor.execute(call, agent="computer")
    assert target.exists()


async def test_a_failing_event_subscriber_cannot_break_a_task(container, office_pc, session_id):
    async def broken(event):
        raise RuntimeError("subscriber bug")

    container.bus.subscribe("*", broken)
    reply = await container.engine.handle_turn(
        session_id=session_id, text="Thursday เปิด chrome", device_id=office_pc.device_id
    )
    assert reply.verified is True


async def test_budget_exhaustion_stops_a_task_cleanly(container, office_pc, session_id):
    from thursday_shared.models import Budget

    task = await container.tasks.create(title="tiny", objective="tiny", budget=Budget(tool_calls=0))
    from thursday_shared.models import Spend

    with pytest.raises(Exception, match="budget"):
        container.tasks.charge(task.id, Spend(tool_calls=1))
