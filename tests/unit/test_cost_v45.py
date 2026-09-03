"""Cost control (§61, Sprint 45).

Per-task budgets were already enforced and are not cost control. They bound one task, and
they only ever saw the agents — the two model calls every turn makes, reasoning and
supervision, were metered nowhere, so the system could run all day and report zero.

So the tests here are about the three things that were missing: that *every* call is counted
including the ones no agent makes, that there is a ceiling above any single task, and that
reaching it degrades rather than stops. Plus the breaker that could never re-open.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from thursday_core.cost import WARN_AT, CostMeter
from thursday_core.model_router import BREAKER_COOLDOWN, BREAKER_TRIP, ModelRouter
from thursday_shared.enums import DataSensitivity, ModelTier
from thursday_shared.errors import BudgetExceeded
from thursday_shared.models import HealthStatus, LLMMessage, LLMRequest, LLMResponse


class StubLLM:
    """A provider that reports a fixed cost, and can be told to fail."""

    def __init__(self, name: str, *, cost: float = 0.01, local: bool = False) -> None:
        self.name = name
        self.local = local
        self.cost = cost
        self.fail = False
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} is down")
        return LLMResponse(
            text="ok", model=self.name, tokens_in=100, tokens_out=50, cost_usd=self.cost
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=not self.fail, detail="")


def ask(text: str = "hello there") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content=text)])


def router(*, cloud_cost: float = 0.01, meter: CostMeter | None = None, local: bool = True):
    cloud = StubLLM("cloud", cost=cloud_cost)
    r = ModelRouter(meter=meter)
    for tier in (ModelTier.FAST, ModelTier.STANDARD, ModelTier.REASONING):
        r.register(tier, cloud)
    free = StubLLM("local", cost=0.0, local=True) if local else None
    if free is not None:
        r.register(ModelTier.LOCAL, free)
    return r, cloud, free


# --------------------------------------------------------------------------- the ledger


async def test_every_model_call_is_counted_including_the_ones_no_agent_makes():
    """The failure this sprint exists for. Spend used to be counted where an agent chose to
    count it, which missed the reasoning and supervision passes — the two calls every single
    turn makes. Metering an opt-in measures the callers who opted in."""
    meter = CostMeter()
    r, _, _ = router(meter=meter)

    await r.complete(ask())  # nobody's agent, nobody's task
    assert meter.spent() == pytest.approx(0.01)
    assert meter.tokens() == 150
    assert len(meter.charges()) == 1


async def test_a_charge_is_attributed_to_the_task_and_agent_that_caused_it():
    meter = CostMeter()
    r, _, _ = router(meter=meter)
    from thursday_shared.ids import new_id

    task = new_id()
    await r.complete(ask(), task_id=task, agent="research")

    charge = meter.charges()[-1]
    assert charge.task_id == task
    assert charge.agent == "research"
    assert meter.spent(task_id=task) == pytest.approx(0.01)
    assert meter.spent(task_id=new_id()) == 0


async def test_the_local_fallback_after_a_provider_failure_is_still_counted():
    """A degraded call is still a call. Counting only the happy path would make an outage
    look like a free day."""
    meter = CostMeter()
    r, cloud, _ = router(meter=meter)
    cloud.fail = True

    _, decision = await r.complete(ask())
    assert decision.fallback_from == "cloud"
    assert len(meter.charges()) == 1
    assert meter.charges()[-1].provider == "local"


async def test_spend_is_reported_by_day_provider_and_period():
    meter = CostMeter()
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    await meter.record(provider="cloud", tier="STANDARD", usd=1.0, now=now)
    await meter.record(provider="cloud", tier="FAST", usd=0.5, now=now - timedelta(days=1))
    await meter.record(provider="local", tier="LOCAL", usd=0.0, now=now)

    assert meter.spent_today(now=now) == pytest.approx(1.0)
    assert meter.spent_this_month(now=now) == pytest.approx(1.5)
    assert meter.by_provider() == {"cloud": pytest.approx(1.5), "local": 0.0}
    assert meter.by_day(now=now)[now.date()] == pytest.approx(1.0)


async def test_the_ledger_does_not_grow_without_bound():
    meter = CostMeter(retention=timedelta(days=7))
    now = datetime(2026, 9, 2, tzinfo=UTC)
    await meter.record(provider="cloud", tier="FAST", usd=1.0, now=now - timedelta(days=30))
    await meter.record(provider="cloud", tier="FAST", usd=2.0, now=now)
    assert [c.usd for c in meter.charges()] == [2.0]


# --------------------------------------------------------------------------- the ceiling


async def test_a_cap_binds_on_spend_no_single_task_would_have_noticed():
    """Nobody sets out to spend a hundred dollars. They spend it forty cents at a time, and
    every individual charge looked reasonable — which is exactly what a per-task budget
    checks and passes."""
    meter = CostMeter(daily_usd=1.0)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    # Thirty charges of four cents. Each one is far inside any sane per-task budget; the
    # count is what does the damage. Deliberately past the cap rather than exactly on it —
    # an assertion sitting on a float boundary tests the arithmetic, not the rule.
    for _ in range(30):
        await meter.record(provider="cloud", tier="FAST", usd=0.04, now=now)
    assert meter.spent_today(now=now) > 1.0

    verdict = meter.check(now=now)
    assert not verdict
    assert verdict.period == "daily"
    assert "daily spending cap" in verdict.reason


async def test_reaching_the_cap_uses_the_local_model_rather_than_refusing():
    """A ceiling that stops Thursday working is worse than the overspend it prevents. The
    owner cannot tell that kind of outage from a broken assistant, so they fix it by removing
    the cap — which is the opposite of the point."""
    meter = CostMeter(daily_usd=0.05)
    r, _cloud, free = router(meter=meter)

    for _ in range(6):
        await r.complete(ask())

    calls_before = free.calls
    response, decision = await r.complete(ask("something new"))
    assert decision.tier is ModelTier.LOCAL
    assert free.calls == calls_before + 1
    assert "cap" in " ".join(decision.reasons)
    assert response.text  # the work still happened


async def test_the_cap_refuses_only_when_there_is_nothing_free_to_fall_back_to():
    """And says so as a budget problem: "the cap is reached" and "the provider is down" want
    different responses from whoever reads it."""
    meter = CostMeter(daily_usd=0.005)
    r, _, _ = router(meter=meter, local=False)
    await r.complete(ask())

    with pytest.raises(BudgetExceeded, match="no local model"):
        await r.complete(ask())


async def test_the_local_model_is_never_capped():
    """It is free, and it is what the cap falls back to. Throttling it would remove the
    thing that keeps a reached cap from being an outage."""
    meter = CostMeter(daily_usd=0.001)
    r, _, free = router(meter=meter)

    for _ in range(5):
        _, decision = await r.complete(ask(), prefer=ModelTier.LOCAL)
        assert decision.tier is ModelTier.LOCAL
    assert free.calls == 5


async def test_a_cap_does_not_override_the_privacy_rule():
    """Degrading toward local is the only direction the cap may push. It must never be a
    reason to send something somewhere it may not go — and local is where SECRET already
    goes, so the two agree, which is worth proving rather than assuming."""
    meter = CostMeter(daily_usd=0.0)
    r, _, _ = router(meter=meter)
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="the password is hunter2")],
        sensitivity=DataSensitivity.SECRET,
    )
    _, decision = await r.complete(request)
    assert decision.tier is ModelTier.LOCAL


async def test_a_new_day_starts_a_new_daily_budget_but_not_a_new_month():
    meter = CostMeter(daily_usd=1.0, monthly_usd=1.25)
    day_one = datetime(2026, 9, 1, 12, tzinfo=UTC)
    for _ in range(16):
        await meter.record(provider="cloud", tier="FAST", usd=0.125, now=day_one)
    assert meter.spent_today(now=day_one) == pytest.approx(2.0)
    assert not meter.check(now=day_one)

    day_two = datetime(2026, 9, 2, 12, tzinfo=UTC)
    assert meter.spent_today(now=day_two) == 0
    verdict = meter.check(now=day_two)
    assert not verdict, "the month should still bind"
    assert verdict.period == "monthly"


async def test_no_cap_configured_means_no_cap_not_a_guess():
    meter = CostMeter()
    await meter.record(provider="cloud", tier="REASONING", usd=1000.0)
    assert meter.check()


def test_nothing_here_can_raise_a_cap():
    """§95's shape applied to money: the ceiling is the owner's, and an agent that finds it
    inconvenient — or a model asked whether to continue — has no method to call."""
    meter = CostMeter(daily_usd=1.0)
    raising = [
        name
        for name in dir(meter)
        if not name.startswith("_")
        and any(word in name for word in ("raise", "set_cap", "increase", "allow", "override"))
    ]
    assert raising == []


# --------------------------------------------------------------------------- warnings


async def test_the_owner_is_warned_before_the_cap_binds_not_after():
    """A warning that arrives with the refusal is not a warning."""
    meter = CostMeter(daily_usd=1.0)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    await meter.record(provider="cloud", tier="FAST", usd=WARN_AT, now=now)

    said = meter.warnings(now=now)
    assert said and "0.80" in said[0]
    assert meter.check(now=now), "still under the cap when the warning is given"


async def test_a_warning_is_said_once_per_period_not_every_turn():
    """One repeated on every turn is one nobody reads, and the first is the one that matters."""
    meter = CostMeter(daily_usd=1.0)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    await meter.record(provider="cloud", tier="FAST", usd=0.9, now=now)

    assert meter.warnings(now=now)
    assert meter.warnings(now=now) == []

    # A new day is a new period: yesterday's warning does not silence today's. Today needs
    # its own spend to warn about, because the daily total genuinely started again.
    tomorrow = now + timedelta(days=1)
    assert meter.warnings(now=tomorrow) == []
    await meter.record(provider="cloud", tier="FAST", usd=0.9, now=tomorrow)
    assert meter.warnings(now=tomorrow) != []


# --------------------------------------------------------------------------- the breaker


async def test_a_tripped_provider_is_tried_again_after_the_cooldown():
    """The bug this replaces: the counter reset only on success, and a parked provider was
    never chosen, so it never succeeded, so it never reset. Three transient failures disabled
    a good provider until somebody restarted the process."""
    r, cloud, _ = router()
    cloud.fail = True
    for _ in range(BREAKER_TRIP):
        await r.complete(ask())
    assert r.parked("cloud")

    later = datetime.now(UTC) + BREAKER_COOLDOWN + timedelta(seconds=1)
    assert not r.parked("cloud", now=later)

    cloud.fail = False
    _, decision = await r.complete(ask("analyse this carefully and explain the trade-offs"))
    assert decision.provider_name == "cloud"
    assert not r.parked("cloud")


async def test_a_provider_that_is_still_broken_is_parked_again():
    """The trial is evidence, not a pardon."""
    r, cloud, _ = router()
    cloud.fail = True
    for _ in range(BREAKER_TRIP):
        await r.complete(ask())

    later = datetime.now(UTC) + BREAKER_COOLDOWN + timedelta(seconds=1)
    assert not r.parked("cloud", now=later)
    await r.complete(ask())  # the trial fails
    assert r.parked("cloud")


async def test_one_success_clears_the_failure_count():
    r, cloud, _ = router()
    cloud.fail = True
    await r.complete(ask())
    cloud.fail = False
    await r.complete(ask())
    assert r._breaker["cloud"] == 0


# --------------------------------------------------------------------------- reporting


async def test_the_summary_says_where_things_stand():
    meter = CostMeter(daily_usd=2.0, monthly_usd=10.0)
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    await meter.record(
        provider="cloud", tier="STANDARD", tokens_in=10, tokens_out=5, usd=0.5, now=now
    )

    summary = meter.summary(now=now)
    assert summary["today_usd"] == pytest.approx(0.5)
    assert summary["daily_cap_usd"] == 2.0
    assert summary["tokens"] == 15
    assert summary["capped"] is False
