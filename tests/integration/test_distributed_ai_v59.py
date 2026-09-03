"""One task across several machines (ADDENDUM §12, §21, §27, §28) — Sprint 59.

Acceptance: "One task uses at least two compute nodes and returns a unified result."

Two properties carry this sprint. The first is the acceptance criterion itself, and it is
straightforward. The second is not, and it is the reason distribution is worth designing
rather than assembling:

**a stage cannot be less private than the task it belongs to.**

Take a SECRET document. Its OCR stage handles an image, its embedding stage handles a vector,
its summary stage handles a paragraph. Judged individually each looks harmless — a vector is
not obviously a secret. But all three are *derived from* the secret, and a vector of a
passphrase reaches the same place the passphrase would. A pipeline that routes each stage on
its own declared sensitivity leaks the document one derivative at a time, and every stage
looks defensible in isolation.
"""

from __future__ import annotations

import pytest
from thursday_core.compute_execution import ComputeExecutor
from thursday_core.compute_router import Candidate, ComputeRouter, RoutingProfile
from thursday_core.distributed import AIJob, DistributedRunner, StageFailed
from thursday_shared.compute import GIB, ComputeProfile, RuntimeKind
from thursday_shared.enums import DataSensitivity
from thursday_shared.ids import new_id

GPU_PC, SERVER, LAPTOP = new_id(), new_id(), new_id()

WORKSTATION = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB)
HEADLESS = ComputeProfile(vram_bytes=0, ram_bytes=64 * GIB, cpu_cores=32)
THIN = ComputeProfile(vram_bytes=0, ram_bytes=16 * GIB)


def model(device_id, name, profile) -> Candidate:
    return Candidate(
        device_id=device_id,
        model_name=name,
        runtime=RuntimeKind.OLLAMA,
        local=True,
        profile=profile,
    )


CLOUD = Candidate(
    device_id=None, model_name="cloud-reasoning", runtime=RuntimeKind.NONE, local=False
)

#: §43's house: a GPU workstation, a headless server, and a laptop.
HOUSE = {
    "ai.vision": [model(GPU_PC, "llava:13b", WORKSTATION)],
    "ai.embedding": [model(SERVER, "nomic-embed-text", HEADLESS)],
    "ai.ocr": [model(LAPTOP, "tesseract-vlm", THIN)],
    "ai.llm": [model(SERVER, "llama3:8b", HEADLESS)],
    "ai.reasoning": [],
}


def router_for(house: dict) -> ComputeRouter:
    router = ComputeRouter()
    router.candidates = lambda capability: list(house.get(capability, []))  # type: ignore[method-assign]
    return router


async def echo(job, step, inputs):
    return {"stage": job.name, "ran_on": step.model, "inputs": sorted(inputs)}


# --------------------------------------------------------------------------- acceptance


async def test_one_task_uses_several_machines_and_returns_one_result():
    """§21's example: vision on the GPU box, embeddings on the server, OCR on the laptop —
    one task, one answer, and a record of which machine did what."""
    runner = DistributedRunner(router_for(HOUSE))

    result = await runner.run(
        [
            AIJob(name="read", capability="ai.ocr"),
            AIJob(name="look", capability="ai.vision", needs=("read",)),
            AIJob(name="index", capability="ai.embedding", needs=("look",)),
        ],
        echo,
        combine=lambda produced: f"{len(produced)} stages completed",
    )

    assert result.distributed is True
    assert result.devices == [LAPTOP, GPU_PC, SERVER]
    assert result.value == "3 stages completed"
    assert [s.job.name for s in result.stages] == ["read", "look", "index"]
    assert result.stage("look").model == "llava:13b"


async def test_each_stage_is_routed_on_its_own_capability():
    """Routing the task once and running everything there would put embeddings on the GPU
    box because that is where the vision model happens to live."""
    runner = DistributedRunner(router_for(HOUSE))
    result = await runner.run(
        [
            AIJob(name="look", capability="ai.vision"),
            AIJob(name="index", capability="ai.embedding"),
        ],
        echo,
    )
    assert result.stage("look").device_id == GPU_PC
    assert result.stage("index").device_id == SERVER


async def test_a_stage_receives_what_the_stages_it_needed_produced():
    """§12's pipeline is a pipeline: OCR feeds embedding feeds summary."""
    runner = DistributedRunner(router_for(HOUSE))
    result = await runner.run(
        [
            AIJob(name="read", capability="ai.ocr"),
            AIJob(name="summarise", capability="ai.llm", needs=("read",)),
        ],
        echo,
    )
    assert result.stage("summarise").value["inputs"] == ["read"]


async def test_the_summary_says_which_machines_touched_the_task():
    """ "Thursday answered" is not good enough for a task that touched three machines."""
    runner = DistributedRunner(router_for(HOUSE))
    result = await runner.run(
        [AIJob(name="read", capability="ai.ocr"), AIJob(name="look", capability="ai.vision")],
        echo,
    )
    summary = result.summary()
    assert summary["distributed"] is True
    assert len(summary["devices"]) == 2
    assert summary["stages"][0]["where"].endswith(f"@{LAPTOP}")


# --------------------------------------------------------------------------- the privacy floor


async def test_a_stage_cannot_be_less_private_than_its_task():
    """The rule that makes distribution safe.

    Every stage here declares PUBLIC — an embedding is "just a vector". The task is SECRET,
    and the vector is a vector *of the secret*, so no stage may leave the machine.
    """
    house = {**HOUSE, "ai.reasoning": [model(SERVER, "local-reasoner", HEADLESS)]}
    runner = DistributedRunner(router_for(house))

    result = await runner.run(
        [
            AIJob(name="read", capability="ai.ocr", sensitivity=DataSensitivity.PUBLIC),
            AIJob(name="index", capability="ai.embedding", sensitivity=DataSensitivity.PUBLIC),
            AIJob(name="think", capability="ai.reasoning", sensitivity=DataSensitivity.PUBLIC),
        ],
        echo,
        sensitivity=DataSensitivity.SECRET,
        cloud=CLOUD,
    )

    assert result.used_cloud is False
    assert all(s.device_id is not None for s in result.stages)


async def test_a_secret_task_fails_a_stage_rather_than_sending_it_to_the_cloud():
    """No local reasoning model exists. The task cannot complete — and that is the correct
    outcome, because the alternative is the one thing forbidden."""
    runner = DistributedRunner(router_for(HOUSE))

    with pytest.raises(StageFailed) as failed:
        await runner.run(
            [AIJob(name="think", capability="ai.reasoning")],
            echo,
            sensitivity=DataSensitivity.SECRET,
            cloud=CLOUD,
        )

    assert failed.value.details["stage"] == "think"
    assert failed.value.details["capability"] == "ai.reasoning"


async def test_a_public_task_may_use_the_cloud_for_the_stage_that_needs_it():
    """§12's "cloud reasoning only if needed" — the same pipeline, without the secret."""
    runner = DistributedRunner(router_for(HOUSE))
    result = await runner.run(
        [
            AIJob(name="read", capability="ai.ocr"),
            AIJob(name="think", capability="ai.reasoning", needs=("read",)),
        ],
        echo,
        sensitivity=DataSensitivity.PUBLIC,
        cloud=CLOUD,
    )

    assert result.stage("read").device_id == LAPTOP
    assert result.stage("think").device_id is None
    assert result.used_cloud is True


async def test_a_stage_may_raise_the_floor_above_its_task_but_not_lower_it():
    """The floor is a maximum of the two, so a stage can be *more* careful than its task."""
    runner = DistributedRunner(router_for(HOUSE))
    result = await runner.run(
        [
            AIJob(
                name="think",
                capability="ai.llm",
                sensitivity=DataSensitivity.SECRET,
            )
        ],
        echo,
        sensitivity=DataSensitivity.PUBLIC,
        cloud=CLOUD,
    )
    assert result.stage("think").device_id == SERVER, "a SECRET stage stayed local"


# --------------------------------------------------------------------------- failure


async def test_a_required_stage_that_cannot_run_fails_the_task_with_the_provenance():
    """§38. Not silent, and the summary shows how far it got."""
    runner = DistributedRunner(router_for({"ai.ocr": HOUSE["ai.ocr"]}))

    with pytest.raises(StageFailed) as failed:
        await runner.run(
            [
                AIJob(name="read", capability="ai.ocr"),
                AIJob(name="look", capability="ai.vision", needs=("read",)),
            ],
            echo,
        )

    summary = failed.value.details["summary"]
    assert summary["stages"][0]["ok"] is True, "the stage that worked is still recorded"
    assert summary["stages"][1]["ok"] is False


async def test_an_optional_stage_that_cannot_run_degrades_rather_than_fails():
    """A stage nobody depends on failing is a less complete answer, not a wrong one."""
    runner = DistributedRunner(router_for(HOUSE))

    result = await runner.run(
        [
            AIJob(name="read", capability="ai.ocr"),
            AIJob(name="think", capability="ai.reasoning", optional=True),
        ],
        echo,
    )

    assert result.stage("read").ok is True
    assert result.stage("think").ok is False
    assert result.distributed is False


async def test_a_stage_whose_input_never_arrived_does_not_run_on_stale_data():
    """The cascade is recorded rather than hidden: a summary built without the OCR that was
    supposed to read the document is not a worse answer, it is a wrong one."""
    runner = DistributedRunner(router_for(HOUSE))

    with pytest.raises(StageFailed, match="did not run"):
        await runner.run(
            [
                AIJob(name="think", capability="ai.reasoning", optional=True),
                AIJob(name="summarise", capability="ai.llm", needs=("think",)),
            ],
            echo,
        )


async def test_a_degraded_stage_is_marked_so_in_the_result():
    """A stage answered by the fallback is a different answer from the one the first choice
    would have given, and the owner is entitled to know which they got."""
    house = {"ai.llm": [model(GPU_PC, "big", WORKSTATION), model(SERVER, "small", HEADLESS)]}
    runner = DistributedRunner(router_for(house), ComputeExecutor())

    async def gpu_is_down(job, step, inputs):
        if step.model == "big":
            raise ConnectionError("the GPU box stopped responding")
        return {"stage": job.name}

    result = await runner.run([AIJob(name="ask", capability="ai.llm")], gpu_is_down)

    assert result.stage("ask").degraded is True
    assert result.stage("ask").device_id == SERVER


async def test_a_profile_on_one_stage_does_not_leak_into_the_next():
    """Each stage is its own routing decision. A LOW_POWER preference for the heavy stage
    must not quietly govern the light one, or the profiles stop meaning anything."""
    house = {
        "ai.llm": [model(LAPTOP, "small", THIN)],
        "ai.vision": [model(GPU_PC, "llava", WORKSTATION)],
    }
    runner = DistributedRunner(router_for(house))
    result = await runner.run(
        [
            AIJob(name="a", capability="ai.llm", profile=RoutingProfile.LOW_POWER),
            AIJob(name="b", capability="ai.vision"),
        ],
        echo,
    )
    assert result.stage("a").device_id == LAPTOP
    assert result.stage("b").device_id == GPU_PC
