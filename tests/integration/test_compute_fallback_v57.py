"""Local/cloud fallback (ADDENDUM §14, §38, §51) — Sprint 57.

Acceptance: "Cloud unavailable → Local model selected. Local unavailable → Cloud selected if
privacy allows." The last four words are the ones with teeth, and they need no code here:
every step of the chain already passed the router's exclusions (ADR 0046), so a fallback
cannot cross a privacy boundary the first choice respected.

The rest is about what "it did not work" means. A machine that is unreachable and a model
whose answer is not good enough are the same event as far as the walk is concerned (§14) —
and neither may end in silence (§38).
"""

from __future__ import annotations

import pytest
from thursday_core.compute_execution import ComputeExecutor, ComputeExhausted
from thursday_core.compute_router import (
    Candidate,
    ComputeRequest,
    ComputeRouter,
    ExecutionTarget,
    RoutingMode,
)
from thursday_core.model_registry import ModelRegistry
from thursday_shared.compute import GIB, ComputeProfile, ModelDescriptor, RuntimeKind
from thursday_shared.enums import DataSensitivity, DeviceStatus
from thursday_shared.ids import new_id

GPU_PC = new_id()
LAPTOP = new_id()

WORKSTATION = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB)
THIN = ComputeProfile(vram_bytes=0, ram_bytes=16 * GIB)


def target(device_id, model: str, *, local: bool = True, fallback=()) -> ExecutionTarget:
    return ExecutionTarget(
        device_id=device_id,
        runtime=RuntimeKind.OLLAMA if local else RuntimeKind.NONE,
        model=model,
        local=local,
        fallback=tuple(fallback),
    )


class Summaries:
    """A hub stand-in: which machines are still registered and online."""

    def __init__(self, online: dict) -> None:
        self._online = online

    def summary(self, device_id):
        return self._online.get(device_id)


class Summary:
    def __init__(self, status=DeviceStatus.ONLINE) -> None:
        self.status = status


# --------------------------------------------------------------------------- the walk


async def test_the_first_target_that_works_is_the_one_used():
    executor = ComputeExecutor()
    chain = target(GPU_PC, "big", fallback=[target(LAPTOP, "small")])

    outcome = await executor.run(chain, lambda step: _answer(step.model))

    assert outcome.value == "answered by big"
    assert outcome.target.device_id == GPU_PC
    assert outcome.degraded is False


async def test_a_failed_target_moves_to_the_next_one():
    executor = ComputeExecutor()
    chain = target(GPU_PC, "big", fallback=[target(LAPTOP, "small")])

    async def flaky(step):
        if step.model == "big":
            raise ConnectionError("the GPU box stopped responding")
        return f"answered by {step.model}"

    outcome = await executor.run(chain, flaky)

    assert outcome.value == "answered by small"
    assert outcome.degraded is True, "the owner is entitled to know this was not first choice"
    assert [a.ok for a in outcome.attempts] == [False, True]
    assert "stopped responding" in outcome.attempts[0].reason


async def test_running_out_of_targets_raises_with_every_reason(  # §38
):
    """ "Thursday could not answer" is only actionable with "the GPU box stopped responding
    and the laptop has no vision model" attached."""
    executor = ComputeExecutor()
    chain = target(GPU_PC, "a", fallback=[target(LAPTOP, "b")])

    async def always_fails(step):
        raise TimeoutError(f"{step.model} timed out")

    with pytest.raises(ComputeExhausted) as exhausted:
        await executor.run(chain, always_fails)

    reasons = " ".join(exhausted.value.details["attempts"])
    assert "a@" in reasons and "b@" in reasons
    assert "timed out" in reasons


async def test_nothing_is_retried_on_the_same_target():
    """§51. A model that just failed on a machine is not likelier to succeed there a second
    later, and moving on is both faster and more likely to work."""
    calls: list[str] = []
    executor = ComputeExecutor()
    chain = target(GPU_PC, "a", fallback=[target(LAPTOP, "b")])

    async def record(step):
        calls.append(step.model)
        raise RuntimeError("no")

    with pytest.raises(ComputeExhausted):
        await executor.run(chain, record)

    assert calls == ["a", "b"], "a target was attempted twice"


# --------------------------------------------------------------------------- §14 quality


async def test_an_answer_that_is_not_good_enough_continues_down_the_chain():
    """§14. A low-confidence local answer may be retried on a stronger model — which is the
    same walk as a failure, so it is the same loop."""
    executor = ComputeExecutor()
    chain = target(LAPTOP, "small", fallback=[target(GPU_PC, "big")])

    async def answer(step):
        return {"model": step.model, "confidence": 0.2 if step.model == "small" else 0.95}

    outcome = await executor.run(chain, answer, acceptable=lambda r: r["confidence"] >= 0.7)

    assert outcome.value["model"] == "big"
    assert outcome.attempts[0].reason == "result did not meet the bar"


async def test_the_best_available_answer_is_returned_when_none_meets_the_bar():
    """A low-confidence answer beats no answer — and `attempts` says it was the last resort,
    so nothing is presented as more certain than it is."""
    executor = ComputeExecutor()
    chain = target(LAPTOP, "small", fallback=[target(GPU_PC, "also-small")])

    outcome = await executor.run(
        chain,
        lambda step: _confidence(step.model, 0.3),
        acceptable=lambda r: r["confidence"] >= 0.9,
    )

    assert outcome.value["confidence"] == 0.3
    assert outcome.degraded is True
    assert all(not a.ok for a in outcome.attempts)


async def test_a_quality_gate_does_not_apply_when_none_is_given():
    executor = ComputeExecutor()
    outcome = await executor.run(target(LAPTOP, "small"), lambda s: _confidence(s.model, 0.01))
    assert outcome.value["confidence"] == 0.01
    assert outcome.attempts[-1].ok is True


# --------------------------------------------------------------------------- staleness


async def test_a_step_whose_machine_left_is_skipped_rather_than_attempted():
    """The chain was computed when the decision was made, and the commonest reason the first
    target fails is that its machine went away. Confirming before attempting turns one dead
    machine into one skipped step instead of one timeout."""
    attempted: list[str] = []
    executor = ComputeExecutor(hub=Summaries({LAPTOP: Summary()}))
    chain = target(GPU_PC, "big", fallback=[target(LAPTOP, "small")])

    async def record(step):
        attempted.append(step.model)
        return f"answered by {step.model}"

    outcome = await executor.run(chain, record)

    assert attempted == ["small"], "the departed machine should not have been called"
    assert outcome.value == "answered by small"
    assert outcome.attempts[0].reason == "the machine is no longer registered"


async def test_a_machine_that_went_offline_is_skipped():
    executor = ComputeExecutor(
        hub=Summaries({GPU_PC: Summary(DeviceStatus.OFFLINE), LAPTOP: Summary()})
    )
    chain = target(GPU_PC, "big", fallback=[target(LAPTOP, "small")])

    outcome = await executor.run(chain, lambda s: _answer(s.model))

    assert outcome.target.device_id == LAPTOP
    assert "went offline" in outcome.attempts[0].reason


async def test_a_model_disabled_between_the_decision_and_the_run_is_skipped():
    """The owner switching a model off is an instruction, and a chain computed a second
    earlier must not route around it."""
    registry = ModelRegistry()
    [entry] = await registry.observe(
        GPU_PC, [ModelDescriptor(name="big", runtime=RuntimeKind.OLLAMA)]
    )
    await registry.observe(LAPTOP, [ModelDescriptor(name="small", runtime=RuntimeKind.OLLAMA)])
    await registry.set_enabled(entry.id, False)

    executor = ComputeExecutor(
        registry=registry, hub=Summaries({GPU_PC: Summary(), LAPTOP: Summary()})
    )
    chain = target(GPU_PC, "big", fallback=[target(LAPTOP, "small")])

    outcome = await executor.run(chain, lambda s: _answer(s.model))

    assert outcome.target.device_id == LAPTOP
    assert "no longer usable" in outcome.attempts[0].reason


async def test_a_cloud_step_is_not_re_checked_against_the_registry():
    """There is no registry entry to consult, and whether the network is up is answered by
    trying rather than by asking."""
    executor = ComputeExecutor(registry=ModelRegistry(), hub=Summaries({}))
    outcome = await executor.run(
        target(None, "cloud-model", local=False), lambda s: _answer(s.model)
    )
    assert outcome.value == "answered by cloud-model"


# --------------------------------------------------------------------------- acceptance


class World:
    def __init__(self, candidates: list[Candidate]) -> None:
        self.router = ComputeRouter()
        self.router.candidates = lambda capability: list(candidates)  # type: ignore[method-assign]


def _local(device_id, name, profile) -> Candidate:
    return Candidate(
        device_id=device_id,
        model_name=name,
        runtime=RuntimeKind.OLLAMA,
        local=True,
        profile=profile,
    )


CLOUD = Candidate(device_id=None, model_name="cloud-model", runtime=RuntimeKind.NONE, local=False)


async def test_cloud_unavailable_falls_back_to_a_local_model():
    """The sprint's first acceptance criterion, decision through execution."""
    world = World([_local(GPU_PC, "llama3:8b", WORKSTATION)])
    chosen = world.router.choose(
        ComputeRequest(mode=RoutingMode.CLOUD_FIRST, sensitivity=DataSensitivity.PUBLIC),
        cloud=CLOUD,
    )
    assert chosen.local is False, "cloud is the first choice while it is up"

    async def network_down(step):
        if not step.local:
            raise ConnectionError("no route to the provider")
        return f"answered by {step.model}"

    outcome = await ComputeExecutor().run(chosen, network_down)

    assert outcome.target.local is True
    assert outcome.value == "answered by llama3:8b"
    assert outcome.degraded is True


def test_no_local_model_falls_back_to_cloud_when_privacy_allows():
    """The second criterion — and "if privacy allows" is the whole of it."""
    world = World([])
    chosen = world.router.choose(
        ComputeRequest(capability="ai.reasoning", sensitivity=DataSensitivity.INTERNAL),
        cloud=CLOUD,
    )
    assert chosen.local is False


def test_no_local_model_and_private_data_does_not_fall_back_to_cloud():
    """The same situation with the privacy bit set is a refusal, not a fallback. This is the
    case that a scoring router would get wrong under load, and it is why the chain is built
    by the filter rather than by preference (ADR 0046)."""
    from thursday_core.compute_router import NoComputeAvailable

    world = World([])
    with pytest.raises(NoComputeAvailable):
        world.router.choose(
            ComputeRequest(capability="ai.reasoning", sensitivity=DataSensitivity.SECRET),
            cloud=CLOUD,
        )


async def test_a_fallback_chain_never_crosses_the_privacy_boundary_under_failure():
    """The property in its most dangerous form: the local machine fails, and there is a
    perfectly good cloud model sitting there. It is still not used, because it never entered
    the chain."""
    world = World([_local(GPU_PC, "local-a", WORKSTATION), _local(LAPTOP, "local-b", THIN)])
    chosen = world.router.choose(ComputeRequest(sensitivity=DataSensitivity.SECRET), cloud=CLOUD)

    async def everything_local_fails(step):
        raise ConnectionError(f"{step.model} is down")

    with pytest.raises(ComputeExhausted) as exhausted:
        await ComputeExecutor().run(chosen, everything_local_fails)

    tried = " ".join(exhausted.value.details["attempts"])
    assert "cloud-model" not in tried
    assert "local-a" in tried and "local-b" in tried


async def _answer(model: str) -> str:
    return f"answered by {model}"


async def _confidence(model: str, value: float) -> dict:
    return {"model": model, "confidence": value}
