"""Planner (§14, §45).

Turns an intent into a DAG of steps. It stays deliberately shallow: most requests are one
or two steps, and a planner that invents six is a planner that will fail four of them. Only
genuinely composite work (analyse → chart → write → check) fans out.
"""

from __future__ import annotations

from thursday.core.logging import get_logger
from thursday.shared.enums import IntentKind, StepKind
from thursday.shared.models import ContextPackage, Intent, Plan, PlanStep

log = get_logger(__name__)


class Planner:
    def __init__(self, *, max_steps: int = 12) -> None:
        self.max_steps = max_steps

    def plan(self, intent: Intent, context: ContextPackage) -> Plan:
        builder = {
            IntentKind.DEVICE_ACTION: self._device_plan,
            IntentKind.FILE_OP: self._device_plan,
            IntentKind.STATUS: self._status_plan,
            IntentKind.RECALL: self._recall_plan,
            IntentKind.SEARCH: self._research_plan,
            IntentKind.ANALYZE: self._analysis_plan,
        }.get(intent.kind, self._empty_plan)

        plan = builder(intent, context)
        if len(plan.steps) > self.max_steps:
            plan.steps = plan.steps[: self.max_steps]
            plan.rationale += f" (truncated to the {self.max_steps}-step limit)"
        for index, step in enumerate(plan.steps):
            step.seq = index
        log.debug("plan_built", intent=str(intent.kind), steps=len(plan.steps))
        return plan

    # ------------------------------------------------------------------ builders

    def _empty_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        """Conversation and direct answers need no execution."""
        return Plan(objective=intent.objective, rationale="answered directly, no execution needed")

    def _device_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        action = str(intent.entities.get("action", ""))
        args = self._args_for(action, intent)
        criteria = ["output.verified is true"]
        if action in ("open_app", "close_app"):
            criteria.append(f"the {action.split('_')[0]} of {args.get('name')} is observable on the device")
        return Plan(
            objective=intent.objective,
            rationale=f"single device action ({action}) delegated to the computer agent",
            steps=[
                PlanStep(
                    seq=0,
                    kind=StepKind.AGENT,
                    name="computer",
                    objective=intent.objective,
                    args={"action": action, "args": args},
                    device_hint=intent.target_device,
                    success_criteria=criteria,
                )
            ],
        )

    def _status_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        if intent.entities.get("subject") == "device":
            # Device status is answered from the hub's own view; asking the device whether
            # it is online is circular, and a silent device is itself the answer.
            return Plan(
                objective=intent.objective,
                rationale="answered from the device registry and world state",
            )
        return Plan(objective=intent.objective, rationale="answered from world state and the task list")

    def _recall_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        return Plan(
            objective=intent.objective,
            rationale="memory lookup before any external source",
            steps=[
                PlanStep(
                    seq=0,
                    kind=StepKind.AGENT,
                    name="research",
                    objective=intent.objective,
                    args={"question": intent.objective, "memory_only": True},
                    success_criteria=["output.answer is not empty", "claims name their source"],
                )
            ],
        )

    def _research_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        return Plan(
            objective=intent.objective,
            rationale="research delegated, with sources required",
            steps=[
                PlanStep(
                    seq=0,
                    kind=StepKind.AGENT,
                    name="research",
                    objective=intent.objective,
                    args={"question": intent.objective},
                    success_criteria=["output.answer is not empty", "claims name their source"],
                )
            ],
        )

    def _analysis_plan(self, intent: Intent, context: ContextPackage) -> Plan:
        """The composite case: gather → analyse → report, each verified in turn."""
        target = intent.entities.get("path") or context.world.last_referenced_file
        gather = PlanStep(
            seq=0,
            kind=StepKind.AGENT,
            name="computer",
            objective=f"locate and read the data for: {intent.objective}",
            args={
                "action": "search_files" if not target else "read_file",
                "args": {"path": target} if target else {"root": "~", "pattern": "*.xlsx"},
            },
            device_hint=intent.target_device,
            success_criteria=["output.verified is true"],
        )
        analyse = PlanStep(
            seq=1,
            kind=StepKind.AGENT,
            name="data",
            objective=f"analyse the data for: {intent.objective}",
            args={"question": intent.objective},
            depends_on=[gather.id],
            success_criteria=["output.summary is not empty", "counts match the underlying rows"],
        )
        report = PlanStep(
            seq=2,
            kind=StepKind.AGENT,
            name="document",
            objective=f"write the report for: {intent.objective}",
            args={},
            depends_on=[analyse.id],
            success_criteria=["output.document is not empty", "claims name their source"],
        )
        return Plan(
            objective=intent.objective,
            rationale="composite analysis: gather, analyse, then report — each step verified",
            steps=[gather, analyse, report],
        )

    def _args_for(self, action: str, intent: Intent) -> dict:
        entities = intent.entities
        if action in ("open_app", "close_app"):
            return {"name": entities.get("app", "")}
        if action in ("open_file", "read_file", "list_dir", "delete"):
            return {"path": entities.get("path", "")}
        if action == "write_file":
            return {"path": entities.get("path", ""), "content": entities.get("content", "")}
        if action == "search_files":
            return {"root": entities.get("root", "~"), "pattern": entities.get("pattern", "*")}
        if action == "run_shell":
            return {"command": entities.get("command", "")}
        return {k: v for k, v in entities.items() if k not in ("action",)}
