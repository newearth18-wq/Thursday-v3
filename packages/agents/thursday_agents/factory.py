"""Dynamic agents (§16).

"Create an agent that grades exam papers" mints a temporary specialist with a goal, a tool
subset, IO schemas, success criteria and a budget. Three guards keep the mechanism from
becoming a way to escape the permission model (§96):

* a dynamic agent's permissions are the **intersection** of the parent's, never a union
* depth and count are capped, so an agent cannot spawn a spawner indefinitely
* the agent is destroyed with its task; only its *spec* survives, and only as a Skill
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from thursday_core.logging import get_logger
from thursday_shared.enums import ModelTier
from thursday_shared.errors import ThursdayError
from thursday_shared.models import (
    AgentResult,
    AgentSpec,
    Budget,
    JobContract,
    LLMMessage,
    LLMRequest,
    PermissionSet,
    ToolCall,
)

from thursday_agents.base import BaseAgent

log = get_logger(__name__)

MAX_PER_TASK = 4
MAX_DEPTH = 2


@dataclass
class DynamicAgentSpec:
    name: str
    goal: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    permissions: PermissionSet = field(default_factory=PermissionSet)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    success_criteria: list[str] = field(default_factory=list)
    timeout_s: float = 120.0
    budget: Budget = field(default_factory=lambda: Budget(usd=0.10, tool_calls=6))


class DynamicAgent(BaseAgent):
    """A temporary specialist. It reasons and uses tools; it never talks to the owner."""

    def __init__(self, definition: DynamicAgentSpec, *, depth: int = 1) -> None:
        self.definition = definition
        self.depth = depth
        self.spec = AgentSpec(
            name=definition.name,
            description=definition.goal,
            capabilities=["dynamic", *definition.tools],
            tools=list(definition.tools),
            permission_ceiling=definition.permissions.max_level,
            default_budget=definition.budget,
            model_tier=ModelTier.STANDARD,
            temporary=True,
            system_prompt=definition.system_prompt,
        )

    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        gathered: list[dict[str, Any]] = []
        for tool in self.definition.tools:
            args = contract.inputs.get(tool) or contract.inputs.get("args") or {}
            if not args:
                continue
            result = await ctx.call_tool(
                ToolCall(
                    tool=tool,
                    args=args,
                    task_id=contract.task_id,
                    step_id=contract.step_id,
                    reason=self.definition.goal,
                )
            )
            gathered.append(
                {"tool": tool, "ok": result.ok, "verified": result.verified, "data": result.data}
            )

        response = await ctx.think(
            LLMRequest(
                messages=[
                    LLMMessage(role="system", content=self.definition.system_prompt),
                    LLMMessage(
                        role="user",
                        content=(
                            f"Goal: {self.definition.goal}\n"
                            f"Inputs: {contract.inputs}\n"
                            f"Tool results: {gathered}\n"
                            f"Success criteria:\n"
                            + "\n".join(f"- {c}" for c in self.definition.success_criteria)
                            + "\n\nProduce the result. If the inputs do not support one, say so."
                        ),
                    ),
                ],
                max_tokens=800,
            )
        )
        return AgentResult(
            agent=self.spec.name,
            ok=True,
            output={"result": response.text, "tool_results": gathered},
            summary=f"{self.definition.name} completed its goal",
            evidence=gathered,
        )


class AgentFactory:
    """Mints and destroys temporary agents, enforcing the caps."""

    def __init__(self, registry: object) -> None:
        self._registry = registry
        self._per_task: dict[UUID, list[str]] = {}

    def create(
        self,
        definition: DynamicAgentSpec,
        *,
        task_id: UUID,
        parent_permissions: PermissionSet | None = None,
        depth: int = 1,
    ) -> DynamicAgent:
        if depth > MAX_DEPTH:
            raise ThursdayError(f"dynamic agents may not nest deeper than {MAX_DEPTH}", depth=depth)
        existing = self._per_task.setdefault(task_id, [])
        if len(existing) >= MAX_PER_TASK:
            raise ThursdayError(
                f"this task has already created {MAX_PER_TASK} dynamic agents",
                task_id=str(task_id),
            )
        if self._registry.has(definition.name):  # type: ignore[attr-defined]
            raise ThursdayError(f"an agent named {definition.name!r} already exists")

        if parent_permissions is not None:
            # Intersection, never union: a child cannot hold what its parent lacked.
            definition.permissions = definition.permissions.intersect(parent_permissions)

        agent = DynamicAgent(definition, depth=depth)
        self._registry.register(agent)  # type: ignore[attr-defined]
        existing.append(definition.name)
        log.info(
            "dynamic_agent_created",
            name=definition.name,
            task_id=str(task_id),
            depth=depth,
            ceiling=definition.permissions.max_level.name,
        )
        return agent

    def destroy_for_task(self, task_id: UUID) -> int:
        """Temporary agents do not outlive their task."""
        names = self._per_task.pop(task_id, [])
        for name in names:
            self._registry.unregister(name)  # type: ignore[attr-defined]
        if names:
            log.info("dynamic_agents_destroyed", count=len(names), task_id=str(task_id))
        return len(names)

    def active_for(self, task_id: UUID) -> list[str]:
        return list(self._per_task.get(task_id, []))
