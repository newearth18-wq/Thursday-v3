"""The compute router (ADDENDUM §7–§9, §13, §15–§19, §46–§47) — Sprint 56.

Acceptance: "Vision task chooses GPU-capable device; small chat chooses lightweight model."

The routing decisions are the easy part. The part worth testing hardest is the distinction the
router is built around: a machine can be **unsuitable** or it can be **forbidden**, and those
are different kinds of reason.

§18 (a busy GPU) and §10 (SECRET must not reach a cloud) both read as "not that one". Make
both scoring inputs and a fast, idle, cheap cloud provider eventually outscores a busy local
box — and the SECRET document leaves the machine because arithmetic said so. A privacy rule
expressed as a large number is a preference. So exclusions filter, preferences rank, and no
score can bring back an excluded candidate.
"""

from __future__ import annotations

import pytest
from thursday_core.compute_router import (
    Candidate,
    ComputeRequest,
    ComputeRouter,
    ExecutionTarget,
    NoComputeAvailable,
    RoutingMode,
    RoutingProfile,
)
from thursday_core.model_registry import ModelRegistry
from thursday_shared.compute import (
    GIB,
    ComputeLoad,
    ComputeProfile,
    ModelDescriptor,
    ModelKind,
    ModelState,
    RuntimeKind,
)
from thursday_shared.enums import DataSensitivity
from thursday_shared.ids import new_id

GPU_PC = new_id()
LAPTOP = new_id()
SERVER = new_id()

WORKSTATION = ComputeProfile(
    gpu_name="NVIDIA RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB, cpu_cores=16
)
THIN_LAPTOP = ComputeProfile(gpu_name="Intel UHD", vram_bytes=0, ram_bytes=16 * GIB, cpu_cores=8)
HEADLESS = ComputeProfile(vram_bytes=0, ram_bytes=64 * GIB, cpu_cores=32)


def local(
    device_id,
    name: str,
    *,
    profile: ComputeProfile,
    load: ComputeLoad | None = None,
    vram: int = 0,
    state: ModelState = ModelState.UNLOADED,
    tps: float = 0.0,
) -> Candidate:
    return Candidate(
        device_id=device_id,
        model_name=name,
        runtime=RuntimeKind.OLLAMA,
        local=True,
        profile=profile,
        load=load,
        state=state,
        tokens_per_second=tps,
        required_vram=vram,
    )


CLOUD = Candidate(
    device_id=None, model_name="cloud-reasoning", runtime=RuntimeKind.NONE, local=False
)


class Registry:
    """Stands in for ModelRegistry + hub, so a routing test states its own world."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates

    def build(self) -> ComputeRouter:
        router = ComputeRouter()
        router.candidates = lambda capability: list(self._candidates)  # type: ignore[method-assign]
        return router


# --------------------------------------------------------------------------- acceptance


def test_a_vision_task_chooses_the_gpu_machine():
    """§9's worked example: laptop has a vision model and is slow, GPU-PC has one and a
    4090. The answer is the GPU box."""
    router = Registry(
        [
            local(LAPTOP, "llava:7b", profile=THIN_LAPTOP),
            local(GPU_PC, "llava:13b", profile=WORKSTATION),
        ]
    ).build()

    target = router.choose(ComputeRequest(capability="ai.vision"))

    assert target.device_id == GPU_PC
    assert target.model == "llava:13b"
    assert any("GPU available" in r for r in target.reasons)


def test_light_chat_does_not_need_the_gpu_box_to_be_free():
    """Small work is not §18's concern: a busy GPU excludes a machine only for heavy work,
    so a quick question still gets answered rather than refused."""
    busy = ComputeLoad(gpu_percent=95.0, queue_depth=6)
    router = Registry([local(GPU_PC, "llama3:8b", profile=WORKSTATION, load=busy)]).build()

    assert router.choose(ComputeRequest(capability="ai.llm", heavy=False)).device_id == GPU_PC
    with pytest.raises(NoComputeAvailable):
        router.choose(ComputeRequest(capability="ai.llm", heavy=True))


def test_a_model_that_does_not_fit_is_never_chosen_however_good_the_machine_looks():
    """The hard floor from §5. An idle machine with 0 VRAM cannot run a 20 GB vision model."""
    router = Registry([local(LAPTOP, "llava:34b", profile=THIN_LAPTOP, vram=20 * GIB)]).build()

    with pytest.raises(NoComputeAvailable, match=r"ai\.vision"):
        router.choose(ComputeRequest(capability="ai.vision"))


# --------------------------------------------------------------------------- forbidden vs unsuitable


def test_secret_content_never_reaches_a_cloud_model_even_when_nothing_local_is_free():
    """The rule that must not be expressible as a score.

    Here the only local machine is thermally throttling and saturated — every preference
    points at the cloud. The answer is still "no", because §10 is a filter.
    """
    dying = ComputeLoad(gpu_percent=99.0, thermal_throttling=True, queue_depth=9)
    router = Registry([local(GPU_PC, "llama3:70b", profile=WORKSTATION, load=dying)]).build()

    with pytest.raises(NoComputeAvailable) as refused:
        router.choose(
            ComputeRequest(capability="ai.llm", sensitivity=DataSensitivity.SECRET, heavy=True),
            cloud=CLOUD,
        )

    reasons = " ".join(refused.value.details["rejected"])
    assert "SECRET content never reaches a cloud provider" in reasons
    assert "thermally throttling" in reasons


def test_highly_private_content_stays_local():
    router = Registry([local(SERVER, "llama3:8b", profile=HEADLESS)]).build()
    target = router.choose(
        ComputeRequest(capability="ai.llm", sensitivity=DataSensitivity.HIGHLY_PRIVATE),
        cloud=CLOUD,
    )
    assert target.local is True
    assert target.device_id == SERVER


def test_local_only_mode_refuses_rather_than_reaching_out(  # §16
):
    """ "The system must report if the work exceeds local capability" — report, not reach."""
    router = Registry([]).build()
    with pytest.raises(NoComputeAvailable) as refused:
        router.choose(
            ComputeRequest(capability="ai.vision", mode=RoutingMode.LOCAL_ONLY), cloud=CLOUD
        )
    assert "LOCAL_ONLY" in " ".join(refused.value.details["rejected"])


@pytest.mark.parametrize("profile", [RoutingProfile.PRIVATE, RoutingProfile.OFFLINE])
def test_the_private_and_offline_profiles_exclude_cloud(profile):
    router = Registry([local(SERVER, "llama3:8b", profile=HEADLESS)]).build()
    target = router.choose(ComputeRequest(capability="ai.llm", profile=profile), cloud=CLOUD)
    assert target.local is True


def test_a_score_cannot_resurrect_an_excluded_candidate():
    """Stated directly, because it is the property the whole design exists for.

    The cloud candidate here is the only one that would satisfy every preference — the local
    machine has no GPU, no warm model and no measured speed. It is still not chosen.
    """
    router = Registry([local(SERVER, "small", profile=HEADLESS)]).build()
    target = router.choose(
        ComputeRequest(capability="ai.llm", sensitivity=DataSensitivity.SECRET), cloud=CLOUD
    )
    assert target.local is True
    assert target.model == "small"


def test_the_owners_explicit_choice_does_not_override_a_hard_limit():
    """§45 respects an explicit instruction — over preferences, not over physics. Asking for
    a machine that cannot hold the model does not make it fit."""
    router = Registry(
        [
            local(LAPTOP, "llava:34b", profile=THIN_LAPTOP, vram=20 * GIB),
            local(GPU_PC, "llava:34b", profile=WORKSTATION, vram=20 * GIB),
        ]
    ).build()

    target = router.choose(ComputeRequest(capability="ai.vision", prefer_device=LAPTOP))
    assert target.device_id == GPU_PC, "the laptop cannot hold it, asked for or not"


def test_the_owners_explicit_choice_wins_among_machines_that_qualify():
    router = Registry(
        [
            local(GPU_PC, "llama3:8b", profile=WORKSTATION),
            local(SERVER, "llama3:8b", profile=HEADLESS),
        ]
    ).build()
    target = router.choose(ComputeRequest(capability="ai.llm", prefer_device=SERVER))
    assert target.device_id == SERVER
    assert any("owner asked" in r for r in target.reasons)


# --------------------------------------------------------------------------- load and power


def test_a_thermally_throttled_machine_is_skipped_for_heavy_work():
    """§18. Low utilisation is what throttling produces, so utilisation alone would read a
    throttled machine as idle and send it more."""
    router = Registry(
        [
            local(
                GPU_PC,
                "big",
                profile=WORKSTATION,
                load=ComputeLoad(gpu_percent=10.0, thermal_throttling=True),
            ),
            local(SERVER, "big", profile=HEADLESS, load=ComputeLoad(gpu_percent=50.0)),
        ]
    ).build()

    assert router.choose(ComputeRequest(capability="ai.llm", heavy=True)).device_id == SERVER


def test_a_machine_with_no_free_vram_right_now_is_skipped_even_though_it_fits():
    """Fitting and being free are different questions, and §18 is the second one."""
    full = ComputeLoad(vram_free_bytes=1 * GIB)
    router = Registry(
        [
            local(GPU_PC, "llava:13b", profile=WORKSTATION, vram=8 * GIB, load=full),
            local(SERVER, "llava:13b", profile=HEADLESS),
        ]
    ).build()

    assert router.choose(ComputeRequest(capability="ai.vision", heavy=True)).device_id == SERVER


def test_a_laptop_on_low_battery_does_not_get_heavy_work():
    """§19. A machine that dies mid-answer has not helped."""
    flat = ComputeLoad(on_battery=True, battery_percent=12.0)
    router = Registry(
        [
            local(LAPTOP, "llama3:8b", profile=THIN_LAPTOP, load=flat),
            local(SERVER, "llama3:8b", profile=HEADLESS),
        ]
    ).build()

    assert router.choose(ComputeRequest(capability="ai.llm", heavy=True)).device_id == SERVER


def test_low_power_profile_keeps_heavy_work_off_battery_even_at_full_charge():
    """§47. The profile is the owner saying "do not cook my laptop", not "wait until it is
    nearly flat"."""
    charged = ComputeLoad(on_battery=True, battery_percent=100.0)
    router = Registry(
        [
            local(LAPTOP, "llama3:8b", profile=THIN_LAPTOP, load=charged),
            local(SERVER, "llama3:8b", profile=HEADLESS),
        ]
    ).build()

    target = router.choose(
        ComputeRequest(capability="ai.llm", profile=RoutingProfile.LOW_POWER, heavy=True)
    )
    assert target.device_id == SERVER


def test_battery_state_does_not_block_light_work():
    """Refusing a one-line answer because a laptop is unplugged would be worse than the
    power it saves."""
    flat = ComputeLoad(on_battery=True, battery_percent=5.0)
    router = Registry([local(LAPTOP, "small", profile=THIN_LAPTOP, load=flat)]).build()
    assert router.choose(ComputeRequest(capability="ai.llm")).device_id == LAPTOP


# --------------------------------------------------------------------------- preference


def test_the_fast_profile_prefers_a_model_that_is_already_loaded():
    """§22. A warm model answers now; a cold one may take a minute to page in."""
    router = Registry(
        [
            local(SERVER, "cold", profile=HEADLESS, state=ModelState.UNLOADED, tps=50),
            local(SERVER, "warm", profile=HEADLESS, state=ModelState.LOADED, tps=40),
        ]
    ).build()

    assert router.choose(ComputeRequest(profile=RoutingProfile.FAST)).model == "warm"


def test_an_unmeasured_model_is_not_treated_as_a_slow_one():
    """`tokens_per_second` is zero until §25's benchmark runs. Reading zero as "slow" would
    be self-fulfilling: the unmeasured model is never chosen, so it is never measured."""
    router = Registry(
        [
            local(SERVER, "measured-mediocre", profile=HEADLESS, tps=5.0),
            local(GPU_PC, "unmeasured", profile=WORKSTATION, tps=0.0),
        ]
    ).build()

    # The GPU machine wins on the GPU it has, not on a speed nobody recorded.
    assert router.choose(ComputeRequest(profile=RoutingProfile.QUALITY)).model == "unmeasured"


def test_cloud_first_still_prefers_cloud_when_privacy_permits():
    router = Registry([local(SERVER, "local", profile=HEADLESS)]).build()
    target = router.choose(
        ComputeRequest(
            capability="ai.llm", mode=RoutingMode.CLOUD_FIRST, sensitivity=DataSensitivity.PUBLIC
        ),
        cloud=CLOUD,
    )
    assert target.local is False


def test_cloud_only_does_not_use_local_models():
    router = Registry([local(SERVER, "local", profile=HEADLESS)]).build()
    target = router.choose(
        ComputeRequest(mode=RoutingMode.CLOUD_ONLY, sensitivity=DataSensitivity.PUBLIC),
        cloud=CLOUD,
    )
    assert target.local is False


# --------------------------------------------------------------------------- the chain


def test_the_fallback_chain_only_contains_candidates_that_passed_the_same_filter():
    """A fallback nobody checked against the privacy rules is a way around them.

    The cloud candidate is excluded for SECRET content, so it must not appear anywhere in the
    chain — not as the choice and not as a fallback the executor would reach on the first
    failure.
    """
    router = Registry(
        [
            local(GPU_PC, "a", profile=WORKSTATION),
            local(SERVER, "b", profile=HEADLESS),
        ]
    ).build()

    target = router.choose(ComputeRequest(sensitivity=DataSensitivity.SECRET), cloud=CLOUD)

    assert all(step.local for step in target.chain())
    assert "cloud-reasoning" not in [step.model for step in target.chain()]
    assert len(target.fallback) == 1


def test_a_failure_says_why_every_candidate_was_rejected():
    """§38 — do not fail silently. The reasons are what turns "Thursday cannot" into
    something the owner can act on."""
    router = Registry([local(LAPTOP, "llava:34b", profile=THIN_LAPTOP, vram=20 * GIB)]).build()

    with pytest.raises(NoComputeAvailable) as refused:
        router.choose(ComputeRequest(capability="ai.vision"))

    assert refused.value.details["capability"] == "ai.vision"
    assert "not enough VRAM" in " ".join(refused.value.details["rejected"])


def test_the_chain_starts_with_the_chosen_target():
    router = Registry([local(SERVER, "only", profile=HEADLESS)]).build()
    target = router.choose(ComputeRequest())
    assert target.chain() == [target]
    assert isinstance(target, ExecutionTarget)


# --------------------------------------------------------------------------- against the registry


async def test_candidates_come_from_the_registry_and_respect_its_corrections():
    """The router asks the registry by capability, so an owner's correction changes routing.

    A model the owner reclassified as vision must become a vision candidate — that is the
    whole point of Sprint 55's correction surviving.
    """
    registry = ModelRegistry()
    [entry] = await registry.observe(
        GPU_PC, [ModelDescriptor(name="house-model-v3", runtime=RuntimeKind.OLLAMA)]
    )
    router = ComputeRouter(registry=registry)

    assert router.candidates("ai.vision") == []

    await registry.set_kind(entry.id, ModelKind.VISION)
    [candidate] = router.candidates("ai.vision")
    assert candidate.model_name == "house-model-v3"
    assert candidate.device_id == GPU_PC


async def test_a_disabled_model_is_not_a_candidate():
    registry = ModelRegistry()
    [entry] = await registry.observe(
        GPU_PC, [ModelDescriptor(name="llama3", runtime=RuntimeKind.OLLAMA)]
    )
    await registry.set_enabled(entry.id, False)

    assert ComputeRouter(registry=registry).candidates("ai.llm") == []


# --------------------------------------------------------------------------- through the app


@pytest.fixture
def client(settings, container):
    from fastapi.testclient import TestClient
    from thursday_api.app import create_app

    container.device_auth.required = False
    app = create_app(settings, container=container)
    app.state.container = container
    with TestClient(app) as http:
        yield http


async def test_routing_is_inspectable_and_says_why(client, container):
    """§44 says the owner should never have to name a machine. This is for the times they
    ask anyway — "why did that go to the laptop?" — and for the months in which routing goes
    subtly wrong without anybody noticing."""
    await container.model_registry.observe(
        GPU_PC,
        [ModelDescriptor(name="llava:13b", kind=ModelKind.VISION, runtime=RuntimeKind.OLLAMA)],
    )

    answer = client.get("/api/v1/compute/route", params={"capability": "ai.vision"}).json()

    assert answer["routed"] is True
    assert answer["target"]["model"] == "llava:13b"
    assert answer["target"]["local"] is True
    assert answer["target"]["reasons"], "a routing decision with no stated reason"


async def test_a_refusal_over_http_carries_the_rejections(client, container):
    """§38 — not silent, and not a bare 404 either. The reasons are what turn "Thursday
    cannot" into something the owner can act on."""
    answer = client.get("/api/v1/compute/route", params={"capability": "ai.vision"}).json()

    assert answer["routed"] is False
    assert "ai.vision" in answer["reason"]
    assert answer["rejected"] == []


def test_an_unknown_routing_option_is_refused_rather_than_defaulted(client):
    """Defaulting a misspelt mode would silently route under a policy nobody chose — and
    the one somebody most likely misspelt is LOCAL_ONLY."""
    refused = client.get("/api/v1/compute/route", params={"mode": "LOCAL_ONLYY"})
    assert refused.status_code == 422
