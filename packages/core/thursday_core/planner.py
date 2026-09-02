"""Planner (§14, §45).

Turns an intent into a DAG of steps. It stays deliberately shallow: most requests are one
or two steps, and a planner that invents six is a planner that will fail four of them. Only
genuinely composite work (analyse → chart → write → check) fans out.
"""

from __future__ import annotations

from thursday_shared.enums import IntentKind, MemoryLayer, StepKind
from thursday_shared.models import ContextPackage, Intent, Plan, PlanStep

from thursday_core.logging import get_logger

log = get_logger(__name__)

#: Agents that produce something the owner will look at. A remembered instruction about
#: formatting belongs on these and nowhere else — attaching "start with a summary table"
#: to a file search is noise, and noise in an objective makes an agent do the wrong thing.
PRODUCING_AGENTS: frozenset[str] = frozenset({"document", "data", "design", "media", "coding"})

#: Below this, a remembered instruction is a guess, and acting on a guess about how the
#: owner wants their work done is worse than asking.
PROCEDURE_MIN_CONFIDENCE = 0.6

#: More than a handful stops being guidance and starts being a second prompt.
MAX_PROCEDURES = 4


class Planner:
    def __init__(self, *, max_steps: int = 12) -> None:
        self.max_steps = max_steps

    def plan(self, intent: Intent, context: ContextPackage) -> Plan:
        builder = {
            IntentKind.COMPUTER_ACTION: self._device_plan,
            IntentKind.DEVICE_CONTROL: self._device_plan,
            IntentKind.FILE_ACTION: self._device_plan,
            IntentKind.BROWSER_ACTION: self._device_plan,
            IntentKind.STATUS: self._status_plan,
            IntentKind.MEMORY_RECALL: self._recall_plan,
            IntentKind.SEARCH: self._research_plan,
            IntentKind.DATA_ANALYSIS: self._analysis_plan,
            IntentKind.MULTI_STEP_TASK: self._analysis_plan,
        }.get(intent.kind, self._empty_plan)

        plan = builder(intent, context)
        self._apply_remembered_procedures(plan, context)
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
        if action in ("app.open", "app.close"):
            verb = "launch" if action == "app.open" else "termination"
            criteria.append(f"the {verb} of {args.get('app')} is observable on the device")
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
        return Plan(
            objective=intent.objective, rationale="answered from world state and the task list"
        )

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

    def _apply_remembered_procedures(self, plan: Plan, context: ContextPackage) -> None:
        """Make "do it the way I asked last time" actually happen (§7, V5).

        The procedural memories are already in the context package — the gap this closes is
        that nothing read them. A stored instruction that never changes what Thursday does
        is a note, not a memory, and the owner who took the trouble to say "these reports
        start with a summary table" would have to say it again every time.

        Applied to the *producing* steps rather than to every step: telling a file search
        to start with a summary table is noise, and noise in an objective is what makes an
        agent do the wrong thing.
        """
        procedures = [
            record
            for record in context.memories
            if record.layer in (MemoryLayer.PROCEDURAL, MemoryLayer.PREFERENCE)
            and record.confidence >= PROCEDURE_MIN_CONFIDENCE
            and record.content.strip()
        ][:MAX_PROCEDURES]
        if not procedures:
            return

        plan.following = [record.content.strip() for record in procedures]
        instructions = "; ".join(plan.following)
        for step in plan.steps:
            if step.name in PRODUCING_AGENTS:
                step.objective = (
                    f"{step.objective}. Follow the owner's standing instructions: {instructions}"
                )
                step.args = {**step.args, "conventions": plan.following}

        # If nothing in the plan produces output, the instructions still belong on the
        # record — silently dropping them would leave no trace that they were considered.
        if not any(s.name in PRODUCING_AGENTS for s in plan.steps):
            plan.rationale += f" (noted, not applied: {instructions})"
        else:
            plan.rationale += f" (following {len(plan.following)} remembered instruction(s))"
        log.debug("plan_follows_procedures", count=len(plan.following))

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
                "action": "file.read" if target else "file.search",
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
            success_criteria=[
                "output.document is not empty",
                # "Not empty" is satisfied by any string, including a model's apology for
                # being offline. This is the criterion that means the report is a report.
                "output.grounded is true",
                "claims name their source",
            ],
        )
        return Plan(
            objective=intent.objective,
            rationale="composite analysis: gather, analyse, then report — each step verified",
            steps=[gather, analyse, report],
        )

    def _args_for(self, action: str, intent: Intent) -> dict:
        """Map the intent's entities onto the node command's argument names."""
        entities = intent.entities
        if action in ("app.open", "app.close"):
            return {"app": entities.get("app", "")}
        if action in ("file.open", "file.read", "file.list", "file.delete"):
            return {"path": entities.get("path", "")}
        if action == "file.write":
            return {"path": entities.get("path", ""), "content": entities.get("content", "")}
        if action == "file.search":
            args = {"root": entities.get("root", "~"), "pattern": entities.get("pattern", "*")}
            if "limit" in entities:
                # "the latest one" is limit=1, and it has to reach the node: trimming a
                # 200-file list in the reply would still have walked and returned all 200.
                args["limit"] = entities["limit"]
            return args
        if action in ("shell.run", "powershell.run"):
            return {"command": entities.get("command", "")}
        if action == "browser.open":
            return {"url": entities.get("url", "")}
        return {k: v for k, v in entities.items() if k != "action"}
