"""Per-device concurrency, and the parallelism it unblocks (ADDENDUM §129) — Sprint 102.

§23 listed "distributed stages run sequentially" as a gap waiting on a per-device
concurrency limit. Measuring it first found the documented blocker was the lesser of two.

**Measured against the shipped code, before any of this existed:**

    THREE CONCURRENT CHOOSE() CALLS LANDED ON: ['machine-A', 'machine-A', 'machine-A']
    DISTINCT MACHINES USED: 1 of 2 available
    ROUTER ATTRS TRACKING DISPATCH: none

Two idle machines, three concurrent routing decisions, one machine. The router's only idea
of "busy" was `ComputeLoad` — telemetry the node sends with its heartbeat — so three stages
dispatched in the same millisecond were all routed against the same pre-dispatch snapshot.

And the second one, which the release notes did not name:

    stage 'alpha' declared needs=() and was handed []
    stage 'beta'  declared needs=() and was handed ['alpha']

A stage could read output it never declared. Sequentially that is invisible. Run the stages
at once and `beta` sees `alpha` or not depending on which coroutine finishes first — the
answer becomes a function of scheduling. That is a correctness problem where saturating a
GPU is a performance one, and it had to be closed first.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from thursday_core.capacity import DeviceCapacity
from thursday_core.compute_execution import ComputeExecutor
from thursday_core.compute_router import Candidate, ComputeRouter
from thursday_core.distributed import AIJob, DistributedRunner
from thursday_shared.compute import GIB, ComputeLoad, ComputeProfile, RuntimeKind
from thursday_shared.ids import new_id

MACHINE_A, MACHINE_B = new_id(), new_id()
BOX = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB)
IDLE = ComputeLoad(gpu_percent=0.0, queue_depth=0)

#: Long enough that a sequential run is unmistakably longer than a concurrent one, short
#: enough that the suite does not notice. Asserted as an ordering, never as a deadline.
WORK_S = 0.05


def model(device_id: object, name: str = "llava:13b") -> Candidate:
    return Candidate(
        device_id=device_id,
        model_name=name,
        runtime=RuntimeKind.OLLAMA,
        local=True,
        profile=BOX,
        load=IDLE,
    )


def house_of(*devices: object) -> dict:
    return {"ai.vision": [model(d) for d in devices]}


def router_for(house: dict, capacity: DeviceCapacity | None = None) -> ComputeRouter:
    router = ComputeRouter(capacity=capacity)
    router.candidates = lambda capability: list(house.get(capability, []))  # type: ignore[method-assign]
    return router


def runner_for(house: dict, capacity: DeviceCapacity) -> DistributedRunner:
    return DistributedRunner(router_for(house, capacity), ComputeExecutor(capacity=capacity))


# ----------------------------------------------------------------- the ledger itself


async def test_a_slot_is_counted_while_held_and_released_afterwards():
    capacity = DeviceCapacity()
    assert capacity.in_flight(MACHINE_A) == 0

    async with capacity.hold(MACHINE_A):
        assert capacity.in_flight(MACHINE_A) == 1
        assert capacity.at_limit(MACHINE_A) is True

    assert capacity.in_flight(MACHINE_A) == 0
    assert capacity.snapshot() == {}


async def test_a_slot_is_released_when_the_work_raises():
    """A machine that stays full because a job failed is a machine nothing can be sent to
    again. The count has to come back down on the error path or the ledger leaks."""
    capacity = DeviceCapacity()

    with pytest.raises(ConnectionError):
        async with capacity.hold(MACHINE_A):
            raise ConnectionError("the GPU box stopped responding")

    assert capacity.in_flight(MACHINE_A) == 0


async def test_a_machine_never_carries_more_than_its_limit():
    """The guarantee, as opposed to the preference: whatever the router decided, one
    machine runs at most `limit` of Thursday's jobs at once."""
    capacity = DeviceCapacity(default_limit=2)
    peak = concurrent = 0

    async def job() -> None:
        nonlocal peak, concurrent
        async with capacity.hold(MACHINE_A):
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(WORK_S)
            concurrent -= 1

    await asyncio.gather(*(job() for _ in range(6)))
    assert peak == 2


async def test_the_cloud_is_never_bounded():
    """A provider runs its own concurrency on hardware Thursday does not own. Capping it
    here would be inventing a limit nobody asked for."""
    capacity = DeviceCapacity(default_limit=1)
    assert capacity.at_limit(None) is False

    async with capacity.hold(None), capacity.hold(None):
        assert capacity.in_flight(None) == 2
        assert capacity.at_limit(None) is False


async def test_a_limit_below_one_is_refused():
    """Zero is not "no concurrency", it is "no work ever runs" — a task that hangs on a
    semaphore nobody can release."""
    with pytest.raises(ValueError):
        DeviceCapacity(default_limit=0)
    with pytest.raises(ValueError):
        DeviceCapacity().set_limit(MACHINE_A, 0)


async def test_a_machine_may_be_told_it_can_carry_more():
    capacity = DeviceCapacity(default_limit=1)
    capacity.set_limit(MACHINE_A, 3)
    assert capacity.limit(MACHINE_A) == 3
    assert capacity.limit(MACHINE_B) == 1


# ----------------------------------------------------------------- the router prefers


async def test_the_router_prefers_the_machine_carrying_less_of_our_own_work():
    """The measured failure: two equally idle machines, and every concurrent decision went
    to the same one because the only load signal was a heartbeat that predates the
    dispatch."""
    capacity = DeviceCapacity(default_limit=4)
    router = router_for(house_of(MACHINE_A, MACHINE_B), capacity)
    from thursday_core.compute_router import ComputeRequest

    first = router.choose(ComputeRequest(capability="ai.vision"))
    async with capacity.hold(first.device_id):
        second = router.choose(ComputeRequest(capability="ai.vision"))

    assert second.device_id != first.device_id


async def test_a_router_with_no_ledger_still_routes():
    """`capacity` is optional. Without one the router reads the machine's telemetry, which
    is exactly what it did before §129 — worse information, not no routing."""
    router = router_for(house_of(MACHINE_A, MACHINE_B))
    from thursday_core.compute_router import ComputeRequest

    assert router.choose(ComputeRequest(capability="ai.vision")).device_id in (
        MACHINE_A,
        MACHINE_B,
    )


async def test_the_owners_explicit_choice_outranks_spreading():
    """§45. A machine the owner named is the machine, busy or not — load is a preference and
    an instruction is not."""
    capacity = DeviceCapacity(default_limit=4)
    router = router_for(house_of(MACHINE_A, MACHINE_B), capacity)
    from thursday_core.compute_router import ComputeRequest

    async with capacity.hold(MACHINE_A):
        chosen = router.choose(ComputeRequest(capability="ai.vision", prefer_device=MACHINE_A))
    assert chosen.device_id == MACHINE_A


# ----------------------------------------------------------------- stages overlap


async def test_independent_stages_run_at_the_same_time():
    """§21's example is naturally concurrent: vision on the GPU box while embeddings run on
    the server. The `needs` graph already said these three had no reason to wait."""
    capacity = DeviceCapacity(default_limit=4)
    runner = runner_for(house_of(MACHINE_A), capacity)

    async def slow(job, step, inputs):
        await asyncio.sleep(WORK_S)
        return job.name

    jobs = [AIJob(name=n, capability="ai.vision") for n in ("a", "b", "c")]
    started = time.perf_counter()
    result = await runner.run(jobs, slow)
    elapsed = time.perf_counter() - started

    assert [s.job.name for s in result.stages] == ["a", "b", "c"]
    assert elapsed < WORK_S * len(jobs), "three independent stages still ran one after another"


async def test_a_stage_still_waits_for_what_it_declared():
    """Concurrency is not reordering. A stage runs only once every name in its `needs` has
    been produced, which is the one edge the planner actually wrote down."""
    capacity = DeviceCapacity(default_limit=4)
    runner = runner_for(house_of(MACHINE_A), capacity)
    order: list[str] = []

    async def record(job, step, inputs):
        order.append(f"start:{job.name}")
        await asyncio.sleep(WORK_S)
        order.append(f"end:{job.name}")
        return job.name

    await runner.run(
        [
            AIJob(name="read", capability="ai.vision"),
            AIJob(name="summarise", capability="ai.vision", needs=("read",)),
        ],
        record,
    )
    assert order == ["start:read", "end:read", "start:summarise", "end:summarise"]


async def test_a_stage_receives_only_what_it_declared():
    """The second measured hole. `beta` declared nothing and was handed `alpha`'s output,
    so an undeclared dependency worked by accident of ordering — and running the stages at
    once would have turned that accident into a coin flip."""
    capacity = DeviceCapacity(default_limit=4)
    runner = runner_for(house_of(MACHINE_A), capacity)
    seen: dict[str, list[str]] = {}

    async def spy(job, step, inputs):
        seen[job.name] = sorted(inputs)
        return f"output-of-{job.name}"

    await runner.run(
        [
            AIJob(name="alpha", capability="ai.vision"),
            AIJob(name="beta", capability="ai.vision"),
            AIJob(name="gamma", capability="ai.vision", needs=("alpha",)),
        ],
        spy,
    )
    assert seen == {"alpha": [], "beta": [], "gamma": ["alpha"]}


async def test_the_whole_task_may_be_bounded_as_well_as_each_machine():
    """`max_parallel` is a ceiling on the task; the ledger is the ceiling on a machine. A
    caller that wants the old behaviour back asks for one."""
    capacity = DeviceCapacity(default_limit=8)
    runner = runner_for(house_of(MACHINE_A), capacity)
    peak = concurrent = 0

    async def slow(job, step, inputs):
        nonlocal peak, concurrent
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(WORK_S)
        concurrent -= 1
        return job.name

    jobs = [AIJob(name=n, capability="ai.vision") for n in ("a", "b", "c", "d")]
    await runner.run(jobs, slow, max_parallel=1)
    assert peak == 1


async def test_the_machine_limit_binds_even_when_the_task_is_unbounded():
    """`max_parallel=0` lets every runnable stage start. What stops four of them landing on
    one GPU is the ledger, not the runner."""
    capacity = DeviceCapacity(default_limit=2)
    runner = runner_for(house_of(MACHINE_A), capacity)
    peak = concurrent = 0

    async def slow(job, step, inputs):
        nonlocal peak, concurrent
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(WORK_S)
        concurrent -= 1
        return job.name

    jobs = [AIJob(name=n, capability="ai.vision") for n in ("a", "b", "c", "d")]
    await runner.run(jobs, slow)
    assert peak == 2


async def test_stages_are_reported_in_the_order_the_plan_listed_them():
    """Whichever coroutine finishes first, the record reads in the caller's order — a result
    that reordered itself by completion time would make two identical runs look different."""
    capacity = DeviceCapacity(default_limit=4)
    runner = runner_for(house_of(MACHINE_A), capacity)

    async def uneven(job, step, inputs):
        await asyncio.sleep(WORK_S if job.name == "first" else 0)
        return job.name

    result = await runner.run(
        [
            AIJob(name="first", capability="ai.vision"),
            AIJob(name="second", capability="ai.vision"),
        ],
        uneven,
    )
    assert [s.job.name for s in result.stages] == ["first", "second"]


async def test_every_slot_is_released_when_the_task_is_over():
    capacity = DeviceCapacity(default_limit=4)
    runner = runner_for(house_of(MACHINE_A, MACHINE_B), capacity)

    async def echo(job, step, inputs):
        return job.name

    await runner.run([AIJob(name=n, capability="ai.vision") for n in ("a", "b", "c")], echo)
    assert capacity.snapshot() == {}
