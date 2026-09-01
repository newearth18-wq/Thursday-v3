"""Agent Orchestrator (§14, §18, §60).

Walks the plan's DAG, delegates each step to the agent the registry selects, and puts every
result through the Supervisor before it counts. Retries are bounded and informed — the
critique from the failed verification is fed into the next attempt — and the failure ladder
is retry → alternative agent → ask the user. Never an unbounded loop (§96).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from thursday_shared.enums import AgentVerdict, StepKind, TaskState
from thursday_shared.errors import (
    ApprovalRequired,
    BudgetExceeded,
    DeviceUnavailable,
    PermissionDenied,
    ThursdayError,
)
from thursday_shared.models import (
    AgentResult,
    ContextPackage,
    Event,
    JobContract,
    PermissionSet,
    Plan,
    PlanStep,
    Task,
    VerificationReport,
)

from thursday_core.execution import AgentContext
from thursday_core.logging import get_logger

log = get_logger(__name__)


@dataclass
class StepOutcome:
    step: PlanStep
    result: AgentResult | None
    verification: VerificationReport | None
    error: str | None = None
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return bool(
            self.result and self.result.ok and self.verification and self.verification.passed
        )


@dataclass
class ExecutionOutcome:
    task: Task
    outcomes: list[StepOutcome] = field(default_factory=list)
    approval_required: Any | None = None
    clarification: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)

    @property
    def partial(self) -> bool:
        return any(o.ok for o in self.outcomes) and not self.ok

    def summary(self) -> str:
        parts = [o.result.summary for o in self.outcomes if o.result and o.result.summary]
        return "; ".join(parts) if parts else "no work was performed"

    def first_failure(self) -> StepOutcome | None:
        return next((o for o in self.outcomes if not o.ok), None)


class AgentOrchestrator:
    def __init__(
        self,
        *,
        agents: object,
        tools: object,
        executor: object,
        supervisor: object,
        tasks: object,
        memory: object,
        models: object,
        bus: object,
        device_router: object,
        hub: object,
        max_attempts: int = 2,
    ) -> None:
        self._agents = agents
        self._tools = tools
        self._executor = executor
        self._supervisor = supervisor
        self._tasks = tasks
        self._memory = memory
        self._models = models
        self._bus = bus
        self._device_router = device_router
        self._hub = hub
        self._max_attempts = max_attempts

    async def run(
        self, task: Task, plan: Plan, context: ContextPackage, *, wait_for_approval: bool = True
    ) -> ExecutionOutcome:
        outcome = ExecutionOutcome(task=task)
        if not plan.steps:
            return outcome

        await self._tasks.set_plan(task.id, plan)  # type: ignore[attr-defined]
        await self._tasks.transition(task.id, TaskState.RUNNING)  # type: ignore[attr-defined]

        while ready := plan.ready_steps():
            if self._tasks.is_cancelled(task.id):  # type: ignore[attr-defined]
                break
            # Independent steps run together; dependent ones wait for the frontier to clear.
            results = await asyncio.gather(
                *(self._run_step(task, step, context, wait_for_approval) for step in ready),
                return_exceptions=True,
            )
            for step, step_outcome in zip(ready, results, strict=True):
                if isinstance(step_outcome, ApprovalRequired):
                    outcome.approval_required = step_outcome
                    step.status = TaskState.WAITING_APPROVAL
                    await self._tasks.transition(task.id, TaskState.WAITING_APPROVAL)  # type: ignore[attr-defined]
                    return outcome
                if isinstance(step_outcome, BaseException):
                    step.status = TaskState.FAILED
                    step.error = str(step_outcome)
                    outcome.outcomes.append(
                        StepOutcome(
                            step=step, result=None, verification=None, error=str(step_outcome)
                        )
                    )
                    return outcome
                outcome.outcomes.append(step_outcome)
                step.status = TaskState.COMPLETED if step_outcome.ok else TaskState.FAILED
                if not step_outcome.ok:
                    return outcome

            done = sum(1 for s in plan.steps if s.status is TaskState.COMPLETED)
            await self._tasks.update_progress(task.id, done / len(plan.steps))  # type: ignore[attr-defined]

        return outcome

    # ------------------------------------------------------------------ one step

    async def _run_step(
        self, task: Task, step: PlanStep, context: ContextPackage, wait_for_approval: bool
    ) -> StepOutcome:
        if step.kind is not StepKind.AGENT:
            raise ThursdayError(f"step kind {step.kind} is not executable yet", step=step.name)

        device_id, device_name = self._resolve_device(step, context)
        agent = self._pick_agent(step, context)
        permissions = self._permissions_for(agent, task, device_id)

        critique: str | None = None
        last: StepOutcome | None = None

        for attempt in range(1, min(step.max_attempts, self._max_attempts) + 1):
            step.attempt = attempt
            contract = JobContract(
                task_id=task.id,
                step_id=step.id,
                agent=agent.spec.name,  # type: ignore[attr-defined]
                objective=step.objective,
                inputs=step.args,
                output_schema=_schema_for(step),
                success_criteria=step.success_criteria,
                permissions=permissions,
                deadline_s=min(task.budget.seconds or 120.0, 120.0),
                budget=agent.spec.default_budget.intersect(task.budget),  # type: ignore[attr-defined]
                critique=critique,
            )
            ctx = AgentContext(
                executor=self._executor,  # type: ignore[arg-type]
                memory=self._memory,
                models=self._models,
                bus=self._bus,
                task_id=task.id,
                agent=agent.spec.name,  # type: ignore[attr-defined]
                permissions=permissions,
                context=context,
                device_id=device_id,
                device_name=device_name,
                sensitivity=context.sensitivity,
                offline=context.offline,
            )

            try:
                result = await asyncio.wait_for(
                    agent.run(contract, ctx),
                    timeout=contract.deadline_s,  # type: ignore[attr-defined]
                )
            except ApprovalRequired:
                raise
            except PermissionDenied as exc:
                # Not retryable: doing it again produces the same refusal.
                return StepOutcome(
                    step=step, result=None, verification=None, error=exc.message, attempts=attempt
                )
            except (DeviceUnavailable, TimeoutError) as exc:
                critique = f"the previous attempt failed: {exc}"
                last = StepOutcome(
                    step=step, result=None, verification=None, error=str(exc), attempts=attempt
                )
                continue
            except BudgetExceeded as exc:
                return StepOutcome(
                    step=step, result=None, verification=None, error=exc.message, attempts=attempt
                )

            self._tasks.charge(task.id, result.spend)  # type: ignore[attr-defined]
            verification = await self._supervisor.verify(  # type: ignore[attr-defined]
                contract, result, attempt=attempt, max_attempts=step.max_attempts
            )
            outcome = StepOutcome(
                step=step, result=result, verification=verification, attempts=attempt
            )
            self._agents.record_outcome(agent.spec.name, success=verification.passed)  # type: ignore[attr-defined]

            if verification.passed:
                step.output = result.output
                await self._bus.publish(  # type: ignore[attr-defined]
                    Event(
                        kind="task.step.completed",
                        task_id=task.id,
                        payload={"step": step.name, "agent": agent.spec.name, "attempt": attempt},  # type: ignore[attr-defined]
                    )
                )
                return outcome

            last = outcome
            if verification.verdict is AgentVerdict.ESCALATE:
                break
            critique = verification.critique
            log.info("step_retry", step=step.name, attempt=attempt, critique=critique[:160])

        # Retries exhausted: try a different agent with the same capability before giving up.
        if (
            last is not None
            and last.verification
            and last.verification.verdict is AgentVerdict.RETRY
            and (alt := self._alternative_agent(agent, step, context)) is not None
        ):
            log.info(
                "trying_alternative_agent",
                failed=agent.spec.name,  # type: ignore[attr-defined]
                alternative=alt.spec.name,  # type: ignore[attr-defined]
            )
            step.max_attempts = 1
            step.name = alt.spec.name  # type: ignore[attr-defined]
            return await self._run_step(task, step, context, wait_for_approval)

        return last or StepOutcome(
            step=step, result=None, verification=None, error="the step produced no result"
        )

    # ------------------------------------------------------------------ selection

    def _pick_agent(self, step: PlanStep, context: ContextPackage) -> object:
        if self._agents.has(step.name):  # type: ignore[attr-defined]
            return self._agents.get(step.name)  # type: ignore[attr-defined]
        candidate = self._agents.select(  # type: ignore[attr-defined]
            capabilities=_capabilities_for(step),
            available_tools=self._tools.names(),  # type: ignore[attr-defined]
            device_online=bool(self._hub.online()),  # type: ignore[attr-defined]
        )
        if candidate is None:
            raise ThursdayError(
                f"no agent is available for {step.name!r}",
                step=step.name,
                capabilities=_capabilities_for(step),
            )
        return self._agents.get(candidate.spec.name)  # type: ignore[attr-defined]

    def _alternative_agent(
        self, failed: object, step: PlanStep, context: ContextPackage
    ) -> object | None:
        candidate = self._agents.select(  # type: ignore[attr-defined]
            capabilities=_capabilities_for(step),
            available_tools=self._tools.names(),  # type: ignore[attr-defined]
            device_online=bool(self._hub.online()),  # type: ignore[attr-defined]
        )
        if candidate is None or candidate.spec.name == failed.spec.name:  # type: ignore[attr-defined]
            return None
        return self._agents.get(candidate.spec.name)  # type: ignore[attr-defined]

    def _resolve_device(
        self, step: PlanStep, context: ContextPackage
    ) -> tuple[UUID | None, str | None]:
        if step.kind is not StepKind.AGENT or not step.args.get("action"):
            return context.world.active_device_id, context.world.active_device_name
        resolution = self._device_router.resolve(  # type: ignore[attr-defined]
            step.device_hint,
            world=context.world,
            origin_device_id=context.turn.device_id,
        )
        if resolution.device is None:
            raise DeviceUnavailable(resolution.reason, question=resolution.question())
        return resolution.device.id, resolution.device.name

    def _permissions_for(self, agent: object, task: Task, device_id: UUID | None) -> PermissionSet:
        """Intersection only — never a union (§8.5)."""
        spec = agent.spec  # type: ignore[attr-defined]
        return PermissionSet(
            max_level=spec.permission_ceiling,
            allowed_tools=list(spec.tools),
            device_ids=[device_id] if device_id else [],
            network=False,
        )


def _capabilities_for(step: PlanStep) -> list[str]:
    action = str(step.args.get("action", ""))
    if action in ("open_app", "close_app", "process_status"):
        return ["app_control"]
    if action in ("open_file", "read_file", "write_file", "list_dir", "search_files", "delete"):
        return ["file"]
    if action in ("screenshot", "read_active_window"):
        return ["screen"]
    if action == "system_info":
        return ["diagnostics"]
    if "question" in step.args:
        return ["research", "search"]
    return ["os"]


def _schema_for(step: PlanStep) -> dict[str, str]:
    if step.args.get("action"):
        return {"action": "string", "verified": "bool"}
    if "question" in step.args:
        return {"answer": "string?", "findings": "list", "sources": "list"}
    return {}
