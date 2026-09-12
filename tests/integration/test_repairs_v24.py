"""The Repair button repairs something — and stops offering what it never could (ADR 0072).

§23 has carried this since Sprint 66: *"Repair Thursday can currently repair nothing."* The
check was real, the security boundary was real, and the three handlers behind the buttons were
`lambda: None`. The verification step is what kept that from reading as success — pressing one
answered *"ลองซ่อมแล้ว แต่ยังไม่กลับมาทำงาน"*, which was true.

Making them real began by asking what the core can actually do, and the answer was **one of
the three**. These tests are as much about the two that were removed as about the one that
works, because a button that cannot work is worse than no button: it teaches people the
buttons do nothing, including the one that does.
"""

from __future__ import annotations

import pytest
from thursday_core import checkup
from thursday_core.model_router import BREAKER_TRIP, ModelRouter
from thursday_core.recovery import SelfRecovery
from thursday_core.repairs import RepairImpossible, register_repairs, switch_model
from thursday_shared.enums import ModelTier
from thursday_shared.errors import ProviderError
from thursday_shared.models import HealthStatus


class Provider:
    """A model provider that is up or down because a test said so."""

    def __init__(self, name: str, *, ok: bool = True, local: bool = True) -> None:
        self.name = name
        self.ok = ok
        self.local = local

    async def health(self) -> HealthStatus:
        return HealthStatus(name=self.name, ok=self.ok, detail="")


@pytest.fixture
def router() -> ModelRouter:
    """A failing cloud provider and a working local one — the case the repair is for."""
    r = ModelRouter(allow_cloud=True)
    r.register(ModelTier.LOCAL, Provider("rule-based", ok=True))
    r.register(ModelTier.STANDARD, Provider("ollama", ok=False, local=False))
    return r


# ------------------------------------------------------------------- the one that is real


async def test_switching_model_parks_the_failing_provider_so_routing_goes_elsewhere(router):
    """The repair, doing something observable.

    Not a new mechanism: the breaker already parks a provider after three consecutive
    failures. What this adds is the route to it — the owner has read a health check and
    pressed Repair, so the evidence the breaker was waiting for is already in.
    """
    assert router.choose(prefer=ModelTier.STANDARD).provider_name == "ollama"

    await switch_model(router)()

    assert router.parked("ollama")
    assert router.choose(prefer=ModelTier.STANDARD).provider_name == "rule-based"


async def test_the_repair_is_verified_against_what_it_restores_not_against_what_broke(container):
    """The decision this sprint turned on.

    Switching leaves the failing provider exactly as failing, so re-checking that provider —
    the obvious thing, and what `repair()` did first — asks whether the part is fixed when the
    owner asked whether Thursday works. Every successful switch would be reported as a failure.
    """
    broken = Provider("ollama", ok=False, local=False)
    container.models.register(ModelTier.STANDARD, broken)

    result = await checkup.repair(container, f"model:{broken.name}", "switch_model")

    assert result["attempted"] is True
    assert result["ok"] is True
    assert result["verified"] is True
    # And the provider it routed around is still exactly as broken as it was.
    assert container.models.parked("ollama")
    assert (await broken.health()).ok is False


async def test_a_repair_that_could_not_help_says_so_rather_than_running_quietly(router):
    """A handler that returns without raising has proved that a function ran.

    `park` refuses to take the last choosable provider with it, so with one provider left
    there is nothing to do — and the repair has to say that rather than return and be read as
    success.
    """
    lonely = ModelRouter(allow_cloud=True)
    lonely.register(ModelTier.LOCAL, Provider("rule-based", ok=False))

    with pytest.raises(RepairImpossible, match="ไม่มี AI ตัวอื่น"):
        await switch_model(lonely)()


async def test_a_repair_never_leaves_thursday_unable_to_think(router):
    """The constraint that makes the repair safe to press.

    Parking every failing provider would, on a Thursday whose only model is down, leave
    `choose` with nothing and raise `ProviderError` at every request afterwards — worse than
    the failure it was called to fix, which is the one thing a repair may never be.
    """
    lonely = ModelRouter(allow_cloud=True)
    lonely.register(ModelTier.LOCAL, Provider("rule-based", ok=False))

    assert lonely.park("rule-based") is False

    # Still choosable, which is the point: a failing model that might answer beats none.
    assert lonely.choose().provider_name == "rule-based"


async def test_a_provider_that_recovered_between_the_check_and_the_button_is_left_alone(router):
    """A button pressed a moment late should not park something that is working again."""
    for provider in router.providers.values():
        provider.ok = True

    with pytest.raises(RepairImpossible, match="ตอบสนองอยู่แล้ว"):
        await switch_model(router)()

    assert not router.parked("ollama")


async def test_the_verification_is_false_when_nothing_healthy_is_left(router):
    """It reports on the machine, not on the repair having run."""
    from thursday_core.repairs import switched_to_a_working_model

    for provider in router.providers.values():
        provider.ok = False

    assert await switched_to_a_working_model(router)() is False


async def test_a_router_with_everything_parked_reports_failure_rather_than_raising(router):
    """`park` is built so the repair cannot cause this, but a router that arrived here some
    other way is still a Thursday that cannot answer, and the owner should be told."""
    from thursday_core.repairs import switched_to_a_working_model

    for name in ("rule-based", "ollama"):
        router._breaker[name] = BREAKER_TRIP
        from datetime import UTC, datetime

        router._tripped_at[name] = datetime.now(UTC)

    with pytest.raises(ProviderError):
        router.choose()
    assert await switched_to_a_working_model(router)() is False


# ------------------------------------------------------------- the two that were removed


@pytest.mark.parametrize(
    ("component", "gone"),
    [("devices", "reconnect_node"), ("queue", "restart_worker")],
)
def test_the_repairs_that_could_never_have_worked_are_no_longer_offered(component, gone):
    """`reconnect_node` and `restart_worker` were wired as placeholders for several sprints.

    Neither could ever have worked. A node **dials the core**, so there is no address to dial
    back — and `devices` is unhealthy only when nothing at all is connected, so there is not
    even a stale session to close. The background worker is a separate process with its own
    container, and starting a process the core does not own is the neighbourhood of "install a
    system component", which is on the never-automatic list.
    """
    label, repair = checkup.describe(component)

    assert repair is None, f"{component} still offers {gone}, which cannot work"
    assert label  # it is still named for the owner; only the button is gone


@pytest.mark.parametrize("component", ["devices", "queue"])
def test_what_a_person_has_to_do_is_said_where_the_button_was(component):
    """Removing a button that cannot work leaves the owner with "ไม่ตอบสนอง" and nothing else.

    The remedy is shown in **normal** mode, unlike `technical`, which is Developer Options.
    That is the whole point of not offering a control: the sentence has to be there instead.
    """
    assert checkup.REMEDIES[component]


async def test_the_container_wires_only_repairs_that_do_something(container):
    """The count is the finding rather than a shortfall in effort."""
    wired = [
        action
        for action in ("reconnect_node", "restart_worker", "switch_model")
        if container.recovery.can(action)
    ]

    assert wired == ["switch_model"]


# ------------------------------------------------- a button is offered only if it is wired


def test_a_permitted_repair_nobody_wired_is_not_offered_as_a_button():
    """Two different questions were being answered by one.

    `is_self_repairable` says a repair is *allowed*. It says nothing about whether anything
    would happen. Offering on that alone produced a button that, when pressed, replied that
    there is no automatic repair for this part — the exact lesson this module says it is
    avoiding, taught by the module itself.
    """
    recovery = SelfRecovery()

    assert recovery.can("restart_worker") is False  # permitted, but wired to nothing
    recovery.register("restart_worker", lambda: None)
    assert recovery.can("restart_worker") is True


def test_a_forbidden_repair_is_never_wireable_whatever_else_changed():
    """The boundary this sprint moved around had better still be where it was."""
    recovery = SelfRecovery()

    with pytest.raises(PermissionError):
        recovery.register("change_security", lambda: None)
    with pytest.raises(PermissionError):
        recovery.register("install_component", lambda: None, verify=lambda: True)
    assert recovery.can("change_security") is False


async def test_a_check_offers_a_repair_only_when_the_container_could_run_it(container):
    """End to end through `check`, which is what the settings screen actually calls."""
    container.models.register(ModelTier.STANDARD, Provider("ollama", ok=False, local=False))

    result = await checkup.check(container)
    offered = {f.component: f.repair for f in result.problems}

    assert offered.get("model:ollama") == "switch_model"
    assert offered.get("devices") is None
    for component, repair in offered.items():
        if repair is not None:
            assert container.recovery.can(repair), f"{component} offers an unwired {repair}"


async def test_register_repairs_does_not_wire_from_the_permitted_list(container):
    """Wiring by looping over `SELF_REPAIRS` would recreate exactly what was removed: a repair
    that exists because it is allowed rather than because it works."""
    recovery = SelfRecovery()
    register_repairs(recovery, router=container.models)

    assert recovery.can("switch_model") is True
    assert recovery.can("retry_request") is False
    assert recovery.can("switch_agent") is False
    assert recovery.can("clear_cache") is False


async def test_the_offer_is_decided_by_wiring_and_not_by_the_table(container, monkeypatch):
    """The predicate itself, with the component table pushed out of the way.

    Written after noticing the first version of these tests could not tell the two apart:
    both repairs that could never work had already been removed from `COMPONENTS`, so
    `devices` carried no button whichever predicate ran, and reverting the fix changed
    nothing. Here `devices` is pointed at a repair that is **permitted and wired to nothing** —
    which is exactly what the two removed ones were — and the button still has to stay away.
    """
    monkeypatch.setitem(checkup.COMPONENTS, "devices", ("การเชื่อมต่อกับเครื่อง", "restart_worker"))
    assert not container.hub.online()  # so `devices` is genuinely a problem
    assert container.recovery.can("restart_worker") is False

    result = await checkup.check(container)
    [devices] = [f for f in result.problems if f.component == "devices"]

    assert devices.repair is None, "offered a button for a repair wired to nothing"

    # And once something is wired to it, the same table entry does produce a button — so the
    # assertion above is about the wiring rather than about `restart_worker` being special.
    container.recovery.register("restart_worker", lambda: None)
    again = await checkup.check(container)
    [devices] = [f for f in again.problems if f.component == "devices"]
    assert devices.repair == "restart_worker"
