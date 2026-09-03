"""Automation Agent (§15, V9).

Reports on what Thursday does on its own, and proposes changes to it. It does not enable,
disable or run anything.

The reason is the one that governs the whole automation subsystem: an automation is standing
authority — a thing that will happen again, without anybody present, possibly at three in the
morning. Creating one is a decision the owner takes, and PART 76's rule that an agent cannot
write the owner's preferences applies with more force here, not less: a preference is a
belief Thursday holds, an automation is an action it will take.

So the agent is a *reader and a proposer*. It can answer "what runs by itself", "why did that
fire", "you seem to do this every Monday, shall I do it for you" — and every one of those
ends with the owner deciding. `AutomationEngine.add` and `enable` are reachable from the API,
where a person is on the other end, and not from here.

That is also why its permission ceiling is READ despite the automations it discusses being
able to do far more. The ceiling describes what *this agent* may do, and reading a list is
all it does.
"""

from __future__ import annotations

from typing import Any

from thursday_shared.enums import ModelTier, PermissionLevel
from thursday_shared.models import AgentResult, AgentSpec, Budget, JobContract

from thursday_agents.base import BaseAgent


class AutomationAgent(BaseAgent):
    spec = AgentSpec(
        name="automation",
        description="Explains what runs by itself and proposes new automations for approval.",
        capabilities=["automation", "routine", "schedule", "explain"],
        tools=[],
        agent_type="specialist",
        supported_input=["question"],
        supported_output=["automations", "proposals", "summary"],
        output_schema={"automations": "list", "proposals": "list", "summary": "string"},
        # READ. Enabling an automation is standing authority and is the owner's to grant.
        permission_ceiling=PermissionLevel.READ,
        default_budget=Budget(seconds=30, tool_calls=0, usd=0.01),
        model_tier=ModelTier.FAST,
        cost_profile="free",
        latency_profile="instant",
        privacy_profile="local_preferred",
        system_prompt=(
            "You describe what the assistant does automatically. You never create, enable "
            "or disable an automation; you propose, and the owner decides."
        ),
    )

    def __init__(self, automations: Any, routines: Any = None, skills: Any = None) -> None:
        super().__init__()
        self._automations = automations
        self._routines = routines
        self._observer = skills

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        existing = [
            {
                "id": str(a.id),
                "name": a.name,
                "trigger": str(getattr(a.trigger, "kind", a.trigger)),
                "enabled": a.enabled,
            }
            for a in self._automations.list()
        ]

        proposals: list[dict[str, Any]] = []
        # Two kinds of suggestion, and they are genuinely different things. A *routine* is a
        # habit in time ("you do this around nine"); a *skill* is a workflow in order ("you
        # do these three, in this sequence"). Reporting them as one list would lose which
        # question the owner is being asked.
        if self._routines is not None:
            proposals += [
                {"kind": "routine", "description": c.describe("en"), "seen": c.occurrences}
                for c in self._routines.unproposed()
            ]
        if self._observer is not None:
            proposals += [
                {"kind": "skill", "description": p.describe("en"), "seen": p.runs}
                for p in self._observer.unproposed()
            ]

        enabled = sum(1 for a in existing if a["enabled"])
        summary = f"{enabled} of {len(existing)} automations are enabled" + (
            f"; {len(proposals)} suggestion(s) waiting on you" if proposals else ""
        )
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={
                "automations": existing,
                "proposals": proposals,
                "summary": summary,
                # Stated, so nothing downstream reads a proposal as a change that happened.
                "changed": False,
            },
            summary=summary,
            evidence=[{"automations": len(existing), "proposals": len(proposals)}],
        )
