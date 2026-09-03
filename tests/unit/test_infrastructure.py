"""State store, queue, test doubles and the browser agent (PART 2, 31, 88, 89)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from thursday_agents.browser import BrowserAgent
from thursday_core.state import InMemoryStateStore, RedisStateStore, build_state_store
from thursday_devices.fake import FakeAdapter, FakeDeviceNode
from thursday_models.llm import MockLLM, RuleBasedLLM
from thursday_shared.ids import new_id
from thursday_shared.models import (
    DeviceAction,
    JobContract,
    LLMMessage,
    LLMRequest,
    ToolResult,
)
from thursday_worker.queue import (
    JOBS,
    DramatiqQueue,
    InProcessQueue,
    build_queue,
    job,
)

# ------------------------------------------------------------------ state store


async def test_state_survives_within_its_ttl_and_not_beyond():
    store = InMemoryStateStore()
    await store.set("device:1", {"online": True}, ttl_s=60)
    assert await store.get("device:1") == {"online": True}

    await store.set("device:2", {"online": True}, ttl_s=-1)
    assert await store.get("device:2") is None


async def test_keys_are_listed_by_prefix():
    store = InMemoryStateStore()
    await store.set("device:1", 1)
    await store.set("device:2", 2)
    await store.set("task:1", 3)
    assert sorted(await store.keys("device:")) == ["device:1", "device:2"]


async def test_a_lock_serialises_access():
    store = InMemoryStateStore()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with store.lock("shared"):
            order.append(f"{name}-in")
            await asyncio.sleep(0.01)
            order.append(f"{name}-out")

    await asyncio.gather(worker("a"), worker("b"))
    # Interleaving would show as a-in, b-in, a-out; serialisation means in/out pair up.
    assert order in (["a-in", "a-out", "b-in", "b-out"], ["b-in", "b-out", "a-in", "a-out"])


async def test_pubsub_reaches_subscribers():
    store = InMemoryStateStore()
    seen: list[dict] = []
    store.subscribe("devices", seen.append)
    await store.publish("devices", {"name": "Office-PC"})
    assert seen == [{"name": "Office-PC"}]


def test_redis_is_chosen_only_when_configured():
    assert build_state_store(None).name == "memory"
    assert isinstance(build_state_store("redis://localhost:6379/0"), RedisStateStore)


# ------------------------------------------------------------------ queue


@job("test_echo")
async def _echo(*, value: str) -> str:
    return value


async def test_a_queued_job_runs_and_its_result_is_readable():
    queue = InProcessQueue()
    job_id = await queue.enqueue("test_echo", value="hello")
    await queue.drain()
    assert queue.result(job_id) == "hello"


async def test_an_unregistered_job_is_refused_rather_than_silently_dropped():
    with pytest.raises(KeyError, match="no job registered"):
        await InProcessQueue().enqueue("nope")


async def test_a_failing_job_is_recorded_not_swallowed():
    @job("test_boom")
    async def boom() -> None:
        raise RuntimeError("as expected")

    queue = InProcessQueue()
    job_id = await queue.enqueue("test_boom")
    await queue.drain()
    assert "as expected" in str(queue.result(job_id))


def test_destructive_jobs_are_never_retried_automatically():
    """PART 62 — a blind retry of a job that already moved a file is a second move."""
    assert "execute_task" in DramatiqQueue.NON_RETRYABLE
    assert "run_automation" in DramatiqQueue.NON_RETRYABLE
    assert "decay_memory" not in DramatiqQueue.NON_RETRYABLE


def test_dramatiq_is_chosen_only_when_redis_is_configured():
    assert build_queue(None).name == "in-process"
    assert build_queue("redis://localhost:6379/0").name == "dramatiq"


def test_the_real_jobs_are_registered():
    assert {"execute_task", "run_automation", "decay_memory"} <= set(JOBS)


# ------------------------------------------------------------------ FakeDeviceNode (PART 88)


async def test_the_fake_node_runs_the_real_executor(tmp_path: Path):
    """Only the machine is imaginary — path confinement and verification are genuine."""
    node = FakeDeviceNode(allowed_roots=[tmp_path])

    result = await node.executor.execute(DeviceAction(action="app.open", args={"app": "chrome"}))
    assert result.ok and result.verified
    assert "chrome" in node.adapter.running

    escape = await node.executor.execute(
        DeviceAction(action="file.write", args={"path": "/etc/x", "content": "x"})
    )
    assert not escape.ok
    assert "outside this node's allowed roots" in escape.error


async def test_the_fake_node_can_be_told_to_fail_invisibly(tmp_path: Path):
    """PART 5.1's case: the command reports success and nothing actually starts."""
    node = FakeDeviceNode(allowed_roots=[tmp_path], fail_launch=True)
    result = await node.executor.execute(DeviceAction(action="app.open", args={"app": "chrome"}))
    assert result.ok is True
    assert result.verified is False
    assert result.succeeded is False


async def test_the_fake_node_can_go_offline_mid_task(tmp_path: Path):
    node = FakeDeviceNode(allowed_roots=[tmp_path], offline_after=1)
    first = await node.executor.execute(DeviceAction(action="app.open", args={"app": "a"}))
    assert first.ok
    second = await node.executor.execute(DeviceAction(action="app.open", args={"app": "b"}))
    assert not second.ok
    assert "offline" in second.error


async def test_capabilities_can_be_narrowed_to_exercise_refusals(tmp_path: Path):
    from thursday_shared.models import DeviceCapabilities

    node = FakeDeviceNode(allowed_roots=[tmp_path], capabilities=DeviceCapabilities.of("file.read"))
    result = await node.executor.execute(DeviceAction(action="app.open", args={"app": "chrome"}))
    assert not result.ok and "does not support" in result.error


# ------------------------------------------------------------------ MockLLM (PART 89)


async def test_the_mock_llm_is_scriptable_and_counts_its_calls():
    llm = MockLLM({"grade": "the average is 72"}, default="nothing scripted")
    assert (
        await llm.complete(LLMRequest(messages=[LLMMessage(role="user", content="grade avg")]))
    ).text == "the average is 72"
    assert (
        await llm.complete(LLMRequest(messages=[LLMMessage(role="user", content="weather")]))
    ).text == "nothing scripted"
    assert len(llm.calls) == 2


async def test_an_unscripted_verification_escalates_rather_than_passing():
    """A test double that invents a PASS would hide the property the system is built on."""
    llm = MockLLM()
    response = await llm.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="judge this")],
            json_schema={"title": "Verification"},
        )
    )
    assert response.structured["verdict"] == "ESCALATE"


async def test_the_offline_tier_also_refuses_to_invent_a_verdict():
    """RuleBasedLLM is a product feature, MockLLM is scaffolding — both stay honest."""
    response = await RuleBasedLLM().complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="judge this")],
            json_schema={"title": "Verification"},
        )
    )
    assert response.structured["verdict"] == "ESCALATE"


# ------------------------------------------------------------------ Browser agent (PART 31)


def contract(**inputs) -> JobContract:
    return JobContract(
        task_id=new_id(),
        step_id=new_id(),
        agent="browser",
        objective="fill in the form",
        inputs=inputs,
    )


class _Ctx:
    """Minimal execution context: records the calls the agent makes."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.spend = None

    async def call_tool(self, call):
        self.calls.append(call.tool)
        return ToolResult(
            call_id=call.id,
            tool=call.tool,
            ok=True,
            verified=True,
            data={"url": "https://example.test/done", "title": "Done", "text": "submitted"},
        )

    async def emit(self, event) -> None:
        return None


async def test_the_browser_agent_refuses_coordinate_selectors():
    """PART 31 — coordinate clicking is not a last resort here, it is absent."""
    result = await BrowserAgent().execute(
        contract(steps=[{"action": "click", "selector": {"x": 412, "y": 388}}]), _Ctx()
    )
    assert not result.ok
    assert "coordinate" in result.error


async def test_the_browser_agent_refuses_actions_outside_its_vocabulary():
    result = await BrowserAgent().execute(contract(steps=[{"action": "eval_js"}]), _Ctx())
    assert not result.ok
    assert "not a browser action" in result.error


async def test_the_browser_agent_records_every_step_it_took():
    ctx = _Ctx()
    result = await BrowserAgent().execute(
        contract(
            steps=[
                {"action": "navigate", "url": "https://example.test"},
                {"action": "type", "label": "Name", "text": "Supakit"},
                {"action": "click", "role": "button", "name": "Continue"},
            ]
        ),
        ctx,
    )
    assert result.ok
    assert ctx.calls == ["browser.navigate", "browser.type", "browser.click"]
    assert len(result.actions_taken) == 3
    # PART 75 / ADR 0010 — page text comes back labelled as untrusted.
    assert result.output["untrusted"] is True


async def test_form_submission_is_an_external_action():
    """Submitting a form reaches outside the machine, so it is approved before it runs."""
    from thursday_agents.browser import BROWSER_TOOL_SPECS
    from thursday_shared.enums import PermissionLevel

    assert BROWSER_TOOL_SPECS["browser.submit"].permission is PermissionLevel.EXTERNAL
    assert BROWSER_TOOL_SPECS["browser.submit"].reversible is False
    assert BROWSER_TOOL_SPECS["browser.read"].permission is PermissionLevel.READ


def test_the_fake_adapter_is_the_shipped_one():
    """conftest re-exports it rather than keeping a copy that drifts."""
    from tests.conftest import FakeAdapter as FromConftest

    assert FromConftest is FakeAdapter
