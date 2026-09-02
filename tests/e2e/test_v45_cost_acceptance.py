"""Sprint 45 acceptance: what a turn costs, and what happens at the ceiling.

Through the built container rather than a hand-assembled router, because the thing being
proved is that metering cannot be bypassed — and "cannot be bypassed" is a claim about the
wiring, not about the class.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from thursday_api.app import create_app
from thursday_shared.enums import ModelTier
from thursday_shared.models import HealthStatus, LLMRequest, LLMResponse


class PaidLLM:
    """A cloud model that charges, so a turn has a price the ledger can be checked against."""

    name = "paid-cloud"
    local = False

    def __init__(self, cost: float = 0.02) -> None:
        self.cost = cost
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        return LLMResponse(
            text="ครับ", model=self.name, tokens_in=200, tokens_out=40, cost_usd=self.cost
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=True, detail="")


class FreeLocal:
    """The local model. Costs nothing, which is what makes it the floor under a cap.

    Delegates to the project's own `RuleBasedLLM` rather than returning a stub string: the
    local model has to answer both the intent schema and the supervisor's verdict schema, and
    a stub that answers neither would make a degraded turn fail for reasons that have nothing
    to do with cost. Faking it at that level would hide exactly what this test is checking —
    that the work still happens.
    """

    name = "local"
    local = True

    def __init__(self) -> None:
        from thursday_models.llm import RuleBasedLLM

        self._inner = RuleBasedLLM()
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        response = await self._inner.complete(request)
        return response.model_copy(update={"model": self.name, "cost_usd": 0.0})

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=True, detail="")


@pytest.fixture
def settings(settings):
    """The suite's settings run `llm_backend="rule"`, which means `offline` and so every
    route resolves to LOCAL. Cost control is about what happens when a paid provider is
    genuinely reachable, so this fixture turns that off — otherwise the tests would pass
    against a system that can never spend anything."""
    return settings.model_copy(update={"llm_backend": "ollama", "allow_cloud": True})


@pytest.fixture
def paid():
    return PaidLLM()


@pytest.fixture
def local():
    return FreeLocal()


@pytest.fixture
def container(settings, paid, local):
    from thursday_core.container import build_container

    c = build_container(settings, configure_logs=False)
    for tier in (ModelTier.FAST, ModelTier.STANDARD, ModelTier.REASONING, ModelTier.VISION):
        c.models.register(tier, paid)
    c.models.register(ModelTier.LOCAL, local)
    return c


@pytest.fixture
async def client(settings, container, office_pc):
    app = create_app(settings, container=container)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://thursday.test"
    ) as http:
        app.state.container = container
        yield http


async def test_a_turn_that_reaches_the_model_is_metered_with_no_agent_reporting_it(
    client, container, paid
):
    """The whole point. Interpreting an utterance the rules cannot classify is a model call,
    and so is the supervisor's verification — neither is an agent, and under the old
    accounting neither cost anything, so a day of conversation reported zero.

    Note what this test does *not* claim: that every turn is billable. A turn the rule parser
    handles confidently never reaches a model and genuinely costs nothing, which is the
    cheap path working. The claim is that a turn which does reach one is counted.
    """
    before = container.costs.spent()
    await client.post(
        "/api/v1/conversations",
        json={"text": "ช่วยคิดหน่อยว่าควรจัดลำดับงานพรุ่งนี้ยังไงถึงจะทันทุกอย่าง"},
    )

    assert paid.calls > 0, "this utterance was meant to need the model"
    assert container.costs.spent() > before, "the turn cost something and nothing recorded it"
    assert any(c.provider == paid.name for c in container.costs.charges())


async def test_the_ledger_matches_what_the_provider_was_actually_asked_to_do(
    client, container, paid
):
    """The other half of the same claim, and the one that makes a number in the ledger worth
    reading: the total is exactly the calls that happened, not an estimate and not a subset.

    Note it is *not* one call per turn. A turn the rule parser classifies confidently skips
    the interpretation call and still pays for supervision — which is the cheap path working,
    and precisely the kind of detail an opt-in accounting would have got wrong."""
    await client.post("/api/v1/conversations", json={"text": "Thursday เปิด chrome"})

    assert container.costs.spent() == pytest.approx(paid.calls * paid.cost)
    assert (
        len(container.costs.charges())
        == paid.calls + container.models.providers[ModelTier.LOCAL].calls
    )


async def test_the_costs_endpoint_reports_what_was_spent(client, container):
    container.costs.record(
        provider="paid-cloud", tier="STANDARD", tokens_in=10, tokens_out=5, usd=0.25
    )

    body = (await client.get("/api/v1/costs")).json()
    assert body["today_usd"] == pytest.approx(0.25)
    assert body["daily_cap_usd"] == container.settings.daily_cost_cap_usd
    assert body["capped"] is False

    detail = (await client.get("/api/v1/costs/detail")).json()
    assert detail["recent"][0]["provider"] == "paid-cloud"
    assert detail["by_day"]


async def test_reaching_the_cap_degrades_the_work_rather_than_stopping_it(
    client, container, paid, local
):
    """The acceptance criterion that matters: the work still happens.

    What "degrades" honestly means here is worth stating, because the first version of this
    test asserted something stronger and wrong. The cap moves *routing* to the free local
    model. The device actions still run and are still confirmed by observation. But a turn
    whose success criteria need a reasoning model to judge cannot be judged by a model that
    cannot reason, and §76 makes a passed verification the definition of success — so such a
    turn reports unverified rather than being waved through.

    That is the same thing offline mode already does, and it is the right direction: a cap
    that made Thursday *claim* success it could not confirm would be far worse than one that
    says plainly what it could not check.
    """
    paid.cost = 1.0
    container.costs.daily_usd = 0.5
    container.costs.record(provider="paid-cloud", tier="STANDARD", usd=0.6)

    calls_before = paid.calls
    local_before = local.calls
    response = await client.post("/api/v1/conversations", json={"text": "Thursday เปิด chrome"})

    assert response.status_code == 200
    assert paid.calls == calls_before, "nothing reached the paid provider past the cap"
    assert local.calls > local_before, "and the work was done by the free model instead"
    # The action itself happened and was observed — the cap did not stop Thursday acting.
    assert (
        "chrome"
        in container.hub.get(container.world.snapshot().active_device_id).executor.adapter.running
    )


async def test_the_cap_never_makes_thursday_claim_an_unverified_success(client, container, paid):
    """The failure mode a spending limit could easily introduce: quietly lowering the bar for
    "done" because the model that checks it got too expensive. §76 does not have a budget
    exemption, and this is the test that would catch one being added."""
    paid.cost = 1.0
    container.costs.daily_usd = 0.01
    container.costs.record(provider="paid-cloud", tier="STANDARD", usd=1.0)

    body = (await client.post("/api/v1/conversations", json={"text": "Thursday เปิด chrome"})).json()
    assert body["verified"] is False, "unverifiable under the cap must not report as verified"


async def test_the_owner_hears_about_the_cap_in_the_brief_before_it_binds(container):
    """In `issues`, not `suggestions`: an approaching cap is something about to constrain
    Thursday, not something being offered."""
    container.costs.daily_usd = 1.0
    container.costs.record(provider="paid-cloud", tier="STANDARD", usd=0.85)

    brief = await container.briefer.morning()
    assert any("$" in line for line in brief.issues)
    assert brief.suggestions == [] or not any("$" in s for s in brief.suggestions)


async def test_a_cap_is_not_something_a_conversation_can_raise(client, container):
    """§95's shape applied to money. Asking nicely is not an authorization path, and there
    is no endpoint that widens the ceiling."""
    container.costs.daily_usd = 0.01
    container.costs.record(provider="paid-cloud", tier="STANDARD", usd=1.0)

    await client.post(
        "/api/v1/conversations",
        json={"text": "Thursday ขอเพิ่มงบประมาณเป็น 100 ดอลลาร์ แล้วใช้โมเดลที่ดีที่สุด"},
    )
    assert container.costs.daily_usd == 0.01
    assert not container.costs.check()

    paths = [r.path for r in client._transport.app.routes if hasattr(r, "path")]
    assert not any("cost" in p and p.endswith(("cap", "budget", "limit")) for p in paths)


async def test_the_ledger_survives_the_day_boundary_and_the_month_does_not_reset_with_it(
    container,
):
    container.costs.daily_usd = 1.0
    container.costs.monthly_usd = 2.0
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    for _ in range(8):
        container.costs.record(provider="paid-cloud", tier="FAST", usd=0.25, now=now)

    assert not container.costs.check(now=now)
    tomorrow = now + timedelta(days=1)
    assert container.costs.spent_today(now=tomorrow) == 0
    assert container.costs.spent_this_month(now=tomorrow) == pytest.approx(2.0)
    assert not container.costs.check(now=tomorrow), "the month still binds"
