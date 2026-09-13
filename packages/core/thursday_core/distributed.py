"""One task, several machines (ADDENDUM §12, §21, §27, §28) — Sprint 59.

§21's example is a task split across the house: the GPU box does vision, the server does
embeddings, the laptop preprocesses files, cloud handles the hard reasoning, and the
supervisor verifies the whole thing. §12 is the same idea as a pipeline — OCR, then
embedding, then summary, then cloud reasoning *only if needed*.

Each stage is routed on its own, because a stage is a different question: `ai.vision` and
`ai.embedding` have different candidates, different hardware requirements and different
machines that can serve them. Routing the task once and running everything there would put
embeddings on the GPU box because the vision model lives there.

**A stage cannot be less private than the task it belongs to.**

This is the rule that makes distribution safe, and it is easy to get wrong in a way that
looks reasonable. Consider a SECRET document: the OCR stage handles an image, the embedding
stage handles a vector, the summarising stage handles a paragraph. Each of those, judged on
its own, might look innocuous — a vector is not obviously a secret. But every one of them is
*derived from* the secret, and a vector of a passphrase reaches the same place the passphrase
would. So each stage is routed with `max(stage.sensitivity, task.sensitivity)`, and a stage
cannot lower the floor its task set.

**Where each stage ran is part of the result.** §28's `AIJobResult` carries the device and the
model, and the unified result keeps all of them. "Thursday answered" is not good enough for a
task that touched four machines: the owner is entitled to know which, and an operator
debugging a wrong answer needs to know where it came from.

Nothing here decides whether an action is permitted. Stages are inference; the Permission
Engine still gates every action a model proposes (§30, §31).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from thursday_shared.enums import DataSensitivity
from thursday_shared.errors import ThursdayError
from thursday_shared.ids import new_id

from thursday_core.compute_execution import ComputeExecutor, ComputeExhausted
from thursday_core.compute_router import ComputeRequest, ExecutionTarget, RoutingProfile
from thursday_core.logging import get_logger

log = get_logger(__name__)


class StageFailed(ThursdayError):
    """A stage could not run anywhere, and the task depends on it."""

    code = "stage_failed"


@dataclass(frozen=True)
class AIJob:
    """§27. One stage of work, and what it needs.

    `optional` is §12's "cloud reasoning only if needed": a stage that cannot run is a
    degraded result rather than a failed task. Required stages are the opposite — a summary
    built without the OCR that was supposed to read the document is not a worse answer, it is
    a wrong one.
    """

    name: str
    capability: str
    id: UUID = field(default_factory=new_id)
    sensitivity: DataSensitivity = DataSensitivity.PUBLIC
    profile: RoutingProfile | None = None
    heavy: bool = False
    optional: bool = False
    #: What this stage needs from earlier ones. Names, not indices: a plan that renumbers
    #: when somebody inserts a stage is a plan whose dependencies quietly shift.
    needs: tuple[str, ...] = ()


@dataclass
class AIJobResult:
    """§28. What one stage produced, and where."""

    job: AIJob
    value: Any = None
    device_id: UUID | None = None
    model: str = ""
    ok: bool = True
    error: str = ""
    degraded: bool = False

    @property
    def where(self) -> str:
        return f"{self.model}@{self.device_id or 'cloud'}" if self.model else "not run"


@dataclass
class DistributedResult:
    """The unified answer, with the provenance of every stage that built it."""

    stages: list[AIJobResult] = field(default_factory=list)
    value: Any = None

    @property
    def devices(self) -> list[UUID]:
        """Every machine that touched this task, in the order they were first used."""
        seen: list[UUID] = []
        for stage in self.stages:
            if stage.device_id is not None and stage.device_id not in seen:
                seen.append(stage.device_id)
        return seen

    @property
    def distributed(self) -> bool:
        return len(self.devices) > 1

    @property
    def used_cloud(self) -> bool:
        return any(s.ok and s.device_id is None and s.model for s in self.stages)

    def stage(self, name: str) -> AIJobResult | None:
        return next((s for s in self.stages if s.job.name == name), None)

    def summary(self) -> dict:
        """What the owner is told about where their task ran."""
        return {
            "stages": [
                {"name": s.job.name, "where": s.where, "ok": s.ok, "degraded": s.degraded}
                for s in self.stages
            ],
            "devices": [str(d) for d in self.devices],
            "distributed": self.distributed,
            "used_cloud": self.used_cloud,
        }


class DistributedRunner:
    """Routes and runs each stage of a task, keeping the provenance."""

    def __init__(self, router: Any, executor: ComputeExecutor | None = None) -> None:
        self._router = router
        self._executor = executor or ComputeExecutor()

    async def run(
        self,
        jobs: list[AIJob],
        work: Callable[[AIJob, ExecutionTarget, dict[str, Any]], Awaitable[Any]],
        *,
        sensitivity: DataSensitivity = DataSensitivity.PUBLIC,
        profile: RoutingProfile = RoutingProfile.BALANCED,
        cloud: Any = None,
        combine: Callable[[dict[str, Any]], Any] | None = None,
        max_parallel: int = 0,
    ) -> DistributedResult:
        """Run the stages, overlapping the ones that declared no reason not to.

        Order is still the caller's, and still checked rather than inferred: a stage runs
        only once every name in its `needs` has been produced. What changed in Sprint 102 is
        that stages whose needs are *already* satisfied no longer wait for each other.
        §21's example — vision on the GPU box while embeddings run on the server — is
        naturally concurrent, and the `needs` graph already carried everything needed to say
        so.

        Nothing here topologically sorts. A wave is the set of not-yet-run stages whose
        declared inputs exist, which uses only edges the planner wrote down; a cycle is
        still the planner's to catch (§53), and appears here as a wave that comes up empty
        with stages left over.

        `max_parallel` bounds how many stages this runner starts at once — a ceiling on the
        whole task, where `DeviceCapacity` is the per-machine one. Zero means every stage
        that can run, runs; the machines are bounded individually and that is the bound that
        protects them.
        """
        produced: dict[str, Any] = {}
        result = DistributedResult()
        outcomes: dict[str, AIJobResult] = {}
        done: set[str] = set()
        gate = asyncio.Semaphore(max_parallel) if max_parallel > 0 else None

        async def run_one(job: AIJob) -> AIJobResult:
            # The floor, not the stage's own claim. A vector derived from a secret reaches
            # the same place the secret would.
            request = ComputeRequest(
                capability=job.capability,
                sensitivity=max(sensitivity, job.sensitivity),
                heavy=job.heavy,
                # The stage's profile if it has one, the task's otherwise. A stage that says
                # nothing inherits rather than resetting to a default nobody chose.
                profile=job.profile or profile,
            )
            # Exactly what this stage declared, and nothing else. Handing it the whole of
            # `produced` let an undeclared dependency work by accident of ordering — and an
            # accident of ordering is not something to run stages concurrently on top of.
            # See ADR 0076.
            inputs = {name: produced[name] for name in job.needs}
            try:
                target = self._router.choose(request, cloud=cloud)

                async def stage_work(step: ExecutionTarget) -> Any:
                    return await work(job, step, dict(inputs))

                outcome = await self._executor.run(target, stage_work)
            except (ComputeExhausted, ThursdayError) as exc:
                return AIJobResult(job=job, ok=False, error=str(exc))
            return AIJobResult(
                job=job,
                value=outcome.value,
                device_id=outcome.target.device_id,
                model=outcome.target.model,
                degraded=outcome.degraded,
            )

        async def guarded(job: AIJob) -> AIJobResult:
            if gate is None:
                return await run_one(job)
            async with gate:
                return await run_one(job)

        while True:
            wave = [j for j in jobs if j.name not in done and set(j.needs) <= produced.keys()]
            if not wave:
                break
            # `strict`: gather returns one result per coroutine, in order. If that ever
            # stopped being true, pairing a stage with another stage's outcome is the kind
            # of bug that reads as a routing mystery, so it fails here instead.
            finished = await asyncio.gather(*(guarded(j) for j in wave))
            for job, stage in zip(wave, finished, strict=True):
                outcomes[job.name] = stage
                done.add(job.name)
                if stage.ok:
                    produced[job.name] = stage.value
                elif job.optional:
                    # §12's "cloud reasoning only if needed". A stage nobody depends on
                    # failing is a less complete answer, not a wrong one.
                    log.info("stage_skipped", stage=job.name, reason=stage.error)

        for job in jobs:
            if job.name in outcomes:
                continue
            # Never became runnable: something it needed did not run. Recorded rather than
            # raised on sight, so the result shows the whole cascade rather than only its
            # head.
            missing = [n for n in job.needs if n not in produced]
            outcomes[job.name] = AIJobResult(
                job=job, ok=False, error=f"missing input: {', '.join(missing)}"
            )

        result.stages = [outcomes[j.name] for j in jobs]

        # Raised in the caller's order, so a wave that lost two stages reports the one the
        # plan listed first rather than whichever coroutine happened to finish first.
        for job in jobs:
            stage = outcomes[job.name]
            if stage.ok or job.optional:
                continue
            if stage.error.startswith("missing input:"):
                raise StageFailed(
                    f"stage {job.name!r} needs "
                    f"{', '.join(n for n in job.needs if n not in produced)}, which did not run",
                    stage=job.name,
                    summary=result.summary(),
                )
            raise StageFailed(
                f"stage {job.name!r} could not run anywhere",
                stage=job.name,
                capability=job.capability,
                summary=result.summary(),
            )

        result.value = combine(produced) if combine else produced
        log.info(
            "distributed_task_complete",
            stages=len(result.stages),
            devices=len(result.devices),
            used_cloud=result.used_cloud,
        )
        return result
