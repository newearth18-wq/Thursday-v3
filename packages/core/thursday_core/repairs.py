"""The repairs behind the Repair button, and what each of them can honestly claim (§59, V10).

Sprint 66 built "Check Thursday / Repair Thursday" and wired the repairs to `lambda: None`.
§23 has said so since, and the verification step (ADR 0012) was what kept that from reading as
success: pressing a button answered *"ลองซ่อมแล้ว แต่ยังไม่กลับมาทำงาน"*, which was true.

Making them real starts by asking what the core can actually do, and the answer is: **one of
the three.** That is the finding, not a shortfall in effort.

`reconnect_node` cannot exist. A node dials the core; the core has no address to dial back
and no way to start a process on somebody else's machine. And the component is all-or-nothing —
`devices` is unhealthy only when *nothing* is connected — so there is not even a stale session
to close. The only thing that brings a node back is the node, or a person at that machine.

`restart_worker` cannot exist either. The background worker is a separate process
(`python -m apps.worker`) with its own container. Restarting it means starting a process the
core does not own, which is the neighbourhood of "install a system component" — on the
never-automatic list for good reasons.

`switch_model` can. The router owns provider selection, in this process, and parking a failing
provider has an immediate and observable effect on what the next request reaches.

So the two that cannot are **not offered**, and what the owner has to do instead is said in
their place. A button that cannot work is worse than no button: it teaches people the buttons
do nothing, including the one that does.
"""

from __future__ import annotations

from typing import Any

from thursday_core.logging import get_logger

log = get_logger(__name__)


class RepairImpossible(Exception):
    """The repair ran and found there was nothing it could do that would help."""


def switch_model(router: Any):
    """Park every provider that is failing its own health check, so routing goes elsewhere.

    The breaker already does this after three consecutive failures (ADR 0028's reasoning).
    This is the same park reached by a different route: the owner has read a health check and
    pressed Repair, so the evidence the breaker was waiting for is already in.

    `park` refuses to take the last choosable provider with it, so a Thursday with one failing
    model is left able to try it rather than raising `ProviderError` at every request. When
    nothing could be parked this raises rather than returning quietly — "I ran and changed
    nothing" reported as a success is the failure mode this whole module exists to avoid.
    """

    async def run() -> None:
        failing = [status.name for status in await router.health() if not status.ok]
        if not failing:
            # Between the check and the button, it recovered. Nothing to do, and saying so is
            # better than parking a provider that is working again.
            raise RepairImpossible("ทุกโมเดลตอบสนองอยู่แล้ว")

        parked = [name for name in failing if router.park(name)]
        if not parked:
            raise RepairImpossible("ไม่มี AI ตัวอื่นให้สลับไป — การพักตัวที่มีปัญหาจะทำให้ไม่เหลือตัวไหนเลย")
        log.info("repair_switch_model", parked=parked)

    return run


def switched_to_a_working_model(router: Any):
    """Whether Thursday can now reach a model that is answering.

    **Not** whether the broken provider came back. It did not — parking it is the repair, and
    the provider is exactly as broken as it was. Re-checking the component would ask whether
    the part is fixed when the owner asked whether Thursday works, and would report every
    successful switch as a failure.

    Derived from the router's own choice and its own health, so it is an observation rather
    than the handler's word for it (ADR 0012).
    """

    async def verify() -> bool | None:
        from thursday_shared.errors import ProviderError

        healthy = {status.name for status in await router.health() if status.ok}
        if not healthy:
            return False
        try:
            decision = router.choose()
        except ProviderError:
            # Every provider parked or unregistered. `park` is built so this cannot be what
            # the repair caused, but a router that arrived here some other way is still a
            # Thursday that cannot answer, and the owner should be told that.
            return False
        return decision.provider_name in healthy

    return verify


def register_repairs(recovery: Any, *, router: Any) -> None:
    """Wire the repairs the core can actually perform. One, at the time of writing.

    Called from the container. Deliberately not a loop over `SELF_REPAIRS`: that list says
    what is *permitted*, and wiring from it would recreate the thing this module was written
    to remove — a repair that exists because it is allowed rather than because it works.
    """
    recovery.register(
        "switch_model",
        switch_model(router),
        verify=switched_to_a_working_model(router),
    )
