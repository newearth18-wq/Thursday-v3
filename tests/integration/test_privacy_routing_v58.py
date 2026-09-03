"""Privacy routing (ADDENDUM §10, §16, §32, §93) — Sprint 58.

Acceptance: **"SECRET input → cloud provider never called."**

That sentence is a claim about *calls*, not about return values, and the difference decides
how it should be tested. A test asserting "the answer came from the local model" passes on a
system that asked the cloud first and discarded the reply — by which point the document has
already left the machine and the damage is done wherever it was logged, cached or trained on.

So every test here holds a spy that records each invocation, and the assertion is that the
spy was never touched. The paths are enumerated deliberately, because a privacy rule holds
only on the paths somebody thought to check: the ordinary route, the degraded route after a
provider fails, the route taken when the spending cap is reached, the compute chain after a
machine goes offline, and LOCAL_ONLY with no local model installed at all.
"""

from __future__ import annotations

import pytest
from thursday_core.compute_execution import ComputeExecutor, ComputeExhausted
from thursday_core.compute_router import (
    Candidate,
    ComputeRequest,
    ComputeRouter,
    NoComputeAvailable,
    RoutingMode,
    RoutingProfile,
)
from thursday_core.cost import CostMeter
from thursday_core.model_router import ModelRouter
from thursday_shared.compute import GIB, ComputeProfile, RuntimeKind
from thursday_shared.enums import DataSensitivity, ModelTier
from thursday_shared.errors import PrivacyViolation
from thursday_shared.ids import new_id
from thursday_shared.models import HealthStatus, LLMMessage, LLMRequest, LLMResponse

GPU_PC = new_id()
WORKSTATION = ComputeProfile(gpu_name="RTX 4090", vram_bytes=24 * GIB, ram_bytes=64 * GIB)


class CloudSpy:
    """A cloud provider that records every approach and answers nothing useful.

    `local = False` is the only thing that matters about it. Everything else exists so that
    a system which *did* call it would look like it worked, and the test would still fail.
    """

    name = "cloud-spy"
    local = False

    def __init__(self) -> None:
        self.calls: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        return LLMResponse(text="the cloud answered", model="cloud-spy")

    async def stream(self, request):  # pragma: no cover - not exercised
        yield "the cloud answered"

    async def health(self) -> HealthStatus:
        return HealthStatus(name="cloud-spy", healthy=True)

    @property
    def touched(self) -> bool:
        return bool(self.calls)


class LocalModel:
    name = "local"
    local = True

    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.fails:
            raise ConnectionError("the local runtime is not answering")
        return LLMResponse(text="the local model answered", model="local")

    async def stream(self, request):  # pragma: no cover
        yield "the local model answered"

    async def health(self) -> HealthStatus:
        return HealthStatus(name="local", healthy=True)


def secret(text: str = "the passphrase is in the safe") -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content=text)], sensitivity=DataSensitivity.SECRET
    )


# --------------------------------------------------------------------------- the model router


async def test_a_secret_prompt_does_not_reach_the_cloud_on_the_ordinary_path():
    cloud, local = CloudSpy(), LocalModel()
    router = ModelRouter(providers={ModelTier.STANDARD: cloud, ModelTier.LOCAL: local})

    response, decision = await router.complete(secret())

    assert response.text == "the local model answered"
    assert decision.tier is ModelTier.LOCAL
    assert cloud.touched is False


async def test_a_secret_prompt_does_not_reach_the_cloud_when_the_local_model_fails():
    """The degraded path. A local runtime that is down must produce an error, never a
    silent promotion to the provider the classification forbids."""
    cloud, local = CloudSpy(), LocalModel(fails=True)
    router = ModelRouter(providers={ModelTier.STANDARD: cloud, ModelTier.LOCAL: local})

    with pytest.raises(ConnectionError):
        await router.complete(secret())

    assert cloud.touched is False


async def test_a_secret_prompt_does_not_reach_the_cloud_when_the_spending_cap_is_reached():
    """The cap degrades paid calls to the local model, which is the safe direction. This
    checks the direction cannot invert: reaching a cap must not make a forbidden provider
    look attractive."""
    cloud, local = CloudSpy(), LocalModel()
    meter = CostMeter(daily_usd=0.0001)
    await meter.record(provider="cloud-spy", tier="STANDARD", usd=10.0)
    router = ModelRouter(providers={ModelTier.STANDARD: cloud, ModelTier.LOCAL: local}, meter=meter)

    response, _ = await router.complete(secret())

    assert response.text == "the local model answered"
    assert cloud.touched is False


async def test_choosing_refuses_outright_when_only_a_cloud_provider_is_registered():
    """No local model and SECRET content is a refusal, not a compromise. Raising beats
    answering, because the only way to answer is the one thing forbidden."""
    cloud = CloudSpy()
    router = ModelRouter(providers={ModelTier.STANDARD: cloud, ModelTier.LOCAL: cloud})

    with pytest.raises(PrivacyViolation):
        router.choose(text="anything", sensitivity=DataSensitivity.SECRET)

    assert cloud.touched is False


async def test_a_secret_prompt_is_not_sent_while_a_breaker_is_open_on_the_local_model():
    """A provider parked by the circuit breaker must not push SECRET work outward. The
    breaker exists to route around a bad provider, and routing around this one is forbidden.
    """
    cloud, local = CloudSpy(), LocalModel(fails=True)
    router = ModelRouter(providers={ModelTier.STANDARD: cloud, ModelTier.LOCAL: local})

    for _ in range(4):
        with pytest.raises(Exception):  # noqa: B017 - any failure counts here
            await router.complete(secret())

    assert cloud.touched is False


# --------------------------------------------------------------------------- the compute router


def _world(candidates: list[Candidate]) -> ComputeRouter:
    router = ComputeRouter()
    router.candidates = lambda capability: list(candidates)  # type: ignore[method-assign]
    return router


CLOUD_CANDIDATE = Candidate(
    device_id=None, model_name="cloud-model", runtime=RuntimeKind.NONE, local=False
)


def test_secret_work_is_never_routed_to_a_cloud_candidate():
    router = _world([])
    with pytest.raises(NoComputeAvailable) as refused:
        router.choose(ComputeRequest(sensitivity=DataSensitivity.SECRET), cloud=CLOUD_CANDIDATE)
    assert "SECRET" in " ".join(refused.value.details["rejected"])


async def test_the_cloud_is_not_reached_when_every_local_machine_fails():
    """The execution half of the same rule, and the shape that would catch a fallback list
    built by preference rather than by the filter."""
    called: list[str] = []
    router = _world(
        [
            Candidate(
                device_id=GPU_PC,
                model_name="local-a",
                runtime=RuntimeKind.OLLAMA,
                local=True,
                profile=WORKSTATION,
            )
        ]
    )
    chosen = router.choose(
        ComputeRequest(sensitivity=DataSensitivity.SECRET), cloud=CLOUD_CANDIDATE
    )

    async def work(step):
        called.append(step.model)
        raise ConnectionError("down")

    with pytest.raises(ComputeExhausted):
        await ComputeExecutor().run(chosen, work)

    assert called == ["local-a"], called
    assert "cloud-model" not in called


@pytest.mark.parametrize("sensitivity", [DataSensitivity.SECRET, DataSensitivity.HIGHLY_PRIVATE])
def test_both_classifications_above_private_stay_on_the_machine(sensitivity):
    """§93 routes HIGHLY_PRIVATE to local by preference and SECRET by prohibition. In this
    router both are exclusions, because "prefer local" is what a scoring function can
    outvote and this one deliberately cannot (ADR 0046)."""
    router = _world(
        [
            Candidate(
                device_id=GPU_PC,
                model_name="local",
                runtime=RuntimeKind.OLLAMA,
                local=True,
                profile=WORKSTATION,
            )
        ]
    )
    assert router.choose(ComputeRequest(sensitivity=sensitivity), cloud=CLOUD_CANDIDATE).local


def test_local_only_mode_refuses_rather_than_reaching_out_when_nothing_is_installed():
    """§16 in its sharpest form: the mode is on, no local model exists, and the work is
    public. Thursday must say it cannot rather than quietly using the cloud it is allowed
    to use for public data in every other mode."""
    router = _world([])
    with pytest.raises(NoComputeAvailable) as refused:
        router.choose(
            ComputeRequest(mode=RoutingMode.LOCAL_ONLY, sensitivity=DataSensitivity.PUBLIC),
            cloud=CLOUD_CANDIDATE,
        )
    assert "LOCAL_ONLY" in " ".join(refused.value.details["rejected"])


@pytest.mark.parametrize("profile", [RoutingProfile.PRIVATE, RoutingProfile.OFFLINE])
def test_the_private_and_offline_profiles_keep_public_work_local_too(profile):
    """§47. Choosing PRIVATE is the owner saying "not the cloud", and it applies to
    everything — a rule that only covered data already classified private would add nothing
    the classification did not already do."""
    router = _world([])
    with pytest.raises(NoComputeAvailable):
        router.choose(
            ComputeRequest(profile=profile, sensitivity=DataSensitivity.PUBLIC),
            cloud=CLOUD_CANDIDATE,
        )


# --------------------------------------------------------------------------- the whole stack


async def test_the_container_refuses_secret_work_to_the_cloud_end_to_end(container):
    """Through the built container rather than a hand-made router, because the wiring is
    where a rule gets bypassed — a guard that only exists in a unit test is a guard the
    deployment does not have."""
    cloud = CloudSpy()
    container.models.register(ModelTier.STANDARD, cloud)
    container.models.register(ModelTier.REASONING, cloud)

    response, decision = await container.models.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="analyse this and explain why it matters")],
            sensitivity=DataSensitivity.SECRET,
        )
    )

    assert cloud.touched is False
    assert decision.tier is ModelTier.LOCAL
    assert response.text
