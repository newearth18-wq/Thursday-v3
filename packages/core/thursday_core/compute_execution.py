"""Running what the compute router chose, and what to do when it does not work.

ADDENDUM §14, §38 and §51 — Sprint 57.

`ComputeRouter.choose` produces a target and an ordered fallback chain. This walks it. The
walk is short and the interesting parts are the four rules around it.

**Failing and being insufficient are the same event here.** §14 says a local model whose
confidence is too low may be retried on a stronger local model or escalated to cloud, subject
to privacy. That is the same walk as "the machine did not answer": try the next target. So a
caller can supply a verdict, and a result the verdict rejects continues down the chain exactly
as an exception does. One loop, not two — and "subject to privacy policy" needs no extra code,
because every step of the chain already passed the router's exclusions (ADR 0046).

**The chain is re-checked, not trusted.** It was computed when the decision was made, and the
usual reason the first target fails is that its machine has gone away — which is also the
reason the second one is about to fail. Each step is confirmed still usable before it is
attempted, so one dead machine costs one timeout rather than four.

**Nothing is retried on the same target.** §51 allows retries for idempotent work, and an
inference call mostly is; but a model that just failed on a machine is not more likely to
succeed on the same machine a second later, and the alternative — moving on — is both faster
and more likely to work. Retrying in place is left to the caller, who knows whether the
failure was transient.

**Exhausting the chain raises.** §38: a routing failure must not be silent, and a task whose
compute vanished is paused or blocked rather than quietly marked done. The error carries every
attempt, because "Thursday could not answer" is only actionable with "the GPU box stopped
responding and the laptop has no vision model" attached.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from thursday_shared.enums import DeviceStatus
from thursday_shared.errors import ThursdayError

from thursday_core.compute_router import ExecutionTarget
from thursday_core.logging import get_logger

log = get_logger(__name__)


class ComputeExhausted(ThursdayError):
    """Every target in the chain was tried and none produced an acceptable result."""

    code = "compute_exhausted"


@dataclass(frozen=True)
class Attempt:
    """One target, tried. Kept whether it worked or not — the failures are the diagnosis."""

    target: ExecutionTarget
    ok: bool
    reason: str = ""

    @property
    def where(self) -> str:
        return f"{self.target.model}@{self.target.device_id or 'cloud'}"


@dataclass
class Outcome[T]:
    """What happened, including what it took to get there."""

    value: T
    target: ExecutionTarget
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True when the first choice did not serve this.

        Worth surfacing rather than hiding: an answer from the laptop's small model is a
        different answer from the one the GPU box would have given, and the owner is entitled
        to know which they got.
        """
        return len(self.attempts) > 1


class ComputeExecutor:
    """Walks a chain until something works."""

    def __init__(self, *, registry: Any = None, hub: Any = None) -> None:
        self._registry = registry
        self._hub = hub

    async def run[T](
        self,
        target: ExecutionTarget,
        work: Callable[[ExecutionTarget], Awaitable[T]],
        *,
        acceptable: Callable[[T], bool] | None = None,
    ) -> Outcome[T]:
        """Try each target in turn; return the first acceptable result.

        `acceptable` is §14's quality gate. A result it rejects is not an error — it is a
        reason to try the next target, and if there is no next target it is still the answer
        that gets returned, because a low-confidence answer beats no answer and the caller
        can see from `attempts` that it was the last resort.
        """
        attempts: list[Attempt] = []
        last: tuple[T, ExecutionTarget] | None = None

        for step in target.chain():
            if (unusable := self._unusable(step)) is not None:
                attempts.append(Attempt(step, ok=False, reason=unusable))
                log.info("compute_step_skipped", where=_where(step), reason=unusable)
                continue

            try:
                value = await work(step)
            except Exception as exc:
                attempts.append(Attempt(step, ok=False, reason=f"{type(exc).__name__}: {exc}"))
                log.warning("compute_step_failed", where=_where(step), error=str(exc))
                continue

            if acceptable is not None and not acceptable(value):
                # §14. Not an error — the model answered, the answer was not good enough.
                attempts.append(Attempt(step, ok=False, reason="result did not meet the bar"))
                last = (value, step)
                log.info("compute_step_insufficient", where=_where(step))
                continue

            attempts.append(Attempt(step, ok=True))
            outcome = Outcome(value=value, target=step, attempts=attempts)
            if outcome.degraded:
                log.info("compute_degraded", chose=_where(step), after=len(attempts) - 1)
            return outcome

        if last is not None:
            # Every target answered and none met the bar. Returning the last answer with the
            # attempts attached beats raising: the owner gets something, and the record says
            # it was the best of a bad set rather than a confident result.
            value, step = last
            log.warning("compute_best_effort", chose=_where(step), attempts=len(attempts))
            return Outcome(value=value, target=step, attempts=attempts)

        raise ComputeExhausted(
            "every machine that could run this failed",
            attempts=[f"{a.where}: {a.reason}" for a in attempts],
        )

    def _unusable(self, step: ExecutionTarget) -> str | None:
        """Whether this step is still worth attempting.

        The chain was built when the decision was made. The commonest reason the first target
        fails is that its machine went away — and that is often the reason the next one will
        fail too. Confirming before attempting turns one dead machine into one skipped step
        rather than one timeout.

        Cloud targets are not re-checked here: there is no registry entry to consult, and
        whether the network is up is answered by trying.
        """
        if not step.local or step.device_id is None:
            return None
        if self._hub is not None:
            summary = self._hub.summary(step.device_id)
            if summary is None:
                return "the machine is no longer registered"
            # Compared against the enum, not against a string. `str(DeviceStatus.OFFLINE)`
            # is "offline", so the upper-case comparison this started as was never true —
            # a staleness check that silently never fired.
            if getattr(summary, "status", None) is DeviceStatus.OFFLINE:
                return "the machine went offline"
        if self._registry is not None:
            # The registry is authoritative when it is present: nothing usable on that
            # machine means the step is not worth an attempt, and that includes the case
            # where the owner disabled the model between the decision and the execution.
            usable = {m.name for m in self._registry.on_device(step.device_id) if m.usable}
            if step.model not in usable:
                return "the model is no longer usable on that machine"
        return None


def _where(step: ExecutionTarget) -> str:
    return f"{step.model}@{step.device_id or 'cloud'}"
