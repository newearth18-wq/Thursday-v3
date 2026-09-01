"""Task state machine, budgets, tool routing and undo (§40–43, §32, §61)."""

from __future__ import annotations

import pytest
from thursday_core.tasks import InvalidTransition, TaskManager
from thursday_core.undo import UndoRegistry, is_reversible
from thursday_shared.enums import AgentVerdict, DataSensitivity, RiskLevel, TaskState
from thursday_shared.errors import BudgetExceeded, ThursdayError
from thursday_shared.ids import new_id
from thursday_shared.models import Budget, Spend, UndoRecord, VerificationReport
from thursday_tools.builtin import register_builtin_tools
from thursday_tools.registry import ToolRegistry, ToolRouter


@pytest.fixture
def tasks() -> TaskManager:
    return TaskManager()


async def test_illegal_transitions_are_refused(tasks):
    task = await tasks.create(title="t", objective="t")
    with pytest.raises(InvalidTransition):
        await tasks.transition(task.id, TaskState.COMPLETED)
    with pytest.raises(InvalidTransition):
        await tasks.transition(task.id, TaskState.VERIFYING)


async def test_a_terminal_task_cannot_be_revived(tasks):
    task = await tasks.create(title="t", objective="t")
    await tasks.cancel(task.id)
    with pytest.raises(InvalidTransition):
        await tasks.transition(task.id, TaskState.RUNNING)


async def test_verification_can_send_a_task_back_to_work(tasks):
    """VERIFYING → RUNNING is legal: a failed check means more work, not success."""
    task = await tasks.create(title="t", objective="t")
    await tasks.transition(task.id, TaskState.PLANNING)
    await tasks.transition(task.id, TaskState.RUNNING)
    await tasks.transition(task.id, TaskState.VERIFYING)
    await tasks.transition(task.id, TaskState.RUNNING)
    assert tasks.get(task.id).status is TaskState.RUNNING


async def test_completion_requires_a_passing_verification(tasks):
    task = await tasks.create(title="t", objective="t")
    await tasks.transition(task.id, TaskState.PLANNING)
    await tasks.transition(task.id, TaskState.RUNNING)
    await tasks.transition(task.id, TaskState.VERIFYING)

    with pytest.raises(ThursdayError):
        await tasks.complete(
            task.id, result={}, verification=VerificationReport(verdict=AgentVerdict.RETRY)
        )
    assert tasks.get(task.id).status is TaskState.VERIFYING

    await tasks.complete(
        task.id, result={"ok": True}, verification=VerificationReport(verdict=AgentVerdict.PASS)
    )
    assert tasks.get(task.id).status is TaskState.COMPLETED
    assert tasks.get(task.id).progress == 1.0


async def test_budgets_are_enforced_and_a_subtask_cannot_exceed_its_parent(tasks):
    parent = await tasks.create(title="p", objective="p", budget=Budget(usd=0.10, tool_calls=5))
    child = await tasks.create(
        title="c", objective="c", parent_task_id=parent.id, budget=Budget(usd=10.0, tool_calls=100)
    )
    assert child.budget.usd == 0.10
    assert child.budget.tool_calls == 5

    tasks.charge(parent.id, Spend(usd=0.05))
    with pytest.raises(BudgetExceeded) as exc:
        tasks.charge(parent.id, Spend(usd=0.09))
    assert exc.value.details["limit"] == 0.10


def test_actions_without_an_inverse_are_treated_as_irreversible():
    assert is_reversible("move") and is_reversible("open_app")
    assert not is_reversible("send_email")
    assert not is_reversible("run_shell")
    assert not is_reversible("purchase")


async def test_undo_runs_the_registered_executor():
    registry = UndoRegistry()
    performed: list[str] = []

    async def executor(record: UndoRecord) -> bool:
        performed.append(record.operation)
        return True

    registry.register_executor("close_app", executor)
    record = registry.record(
        UndoRecord(action_id=new_id(), operation="close_app", args={"name": "chrome"})
    )
    assert await registry.undo(record.action_id) is True
    assert performed == ["close_app"]
    # Once undone it is gone, so a double-undo cannot re-run the reversal.
    with pytest.raises(ThursdayError):
        await registry.undo(record.action_id)


async def test_undo_without_an_executor_reports_rather_than_pretending():
    registry = UndoRegistry()
    record = registry.record(UndoRecord(action_id=new_id(), operation="unmapped_operation"))
    with pytest.raises(ThursdayError, match="no executor"):
        await registry.undo(record.action_id)


@pytest.fixture
def tools() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry, hub=object(), memory=None, vault=None)
    return registry


def test_a_duplicate_tool_name_is_refused(tools):
    from thursday_tools.builtin import ClockTool

    with pytest.raises(ValueError, match="already registered"):
        tools.register(ClockTool())


def test_the_router_will_not_send_secret_data_to_a_cloud_tool(tools):
    router = ToolRouter(tools)
    chosen = router.select("research", sensitivity=DataSensitivity.SECRET)
    assert chosen is None  # web_search is the only research tool, and it is not local


def test_the_router_respects_a_risk_ceiling(tools):
    router = ToolRouter(tools)
    assert router.select("shell", max_risk=RiskLevel.HIGH).name == "run_shell"
    assert router.select("shell", max_risk=RiskLevel.LOW) is None


def test_the_router_honours_an_allowlist(tools):
    router = ToolRouter(tools)
    assert router.select("file", allowed=["read_file"]).name == "read_file"
    assert router.select("file", allowed=["nothing_here"]) is None


def test_an_unknown_tool_names_what_is_available(tools):
    from thursday_shared.errors import ToolNotFound

    with pytest.raises(ToolNotFound) as exc:
        tools.get("no_such_tool")
    assert "clock" in exc.value.details["available"]
