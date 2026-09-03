"""Tool Registry and Router (§32).

Every tool declares what it can do, what it costs, how long it takes, how risky it is, and
what permission it needs. The router picks from those declarations — task fit first, then
the cheapest, lowest-risk, lowest-control-tier option that can actually do the job (§19).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from thursday_core.logging import get_logger
from thursday_shared.enums import ControlTier, DataSensitivity, RiskLevel
from thursday_shared.errors import ToolNotFound
from thursday_shared.models import ToolSpec

log = get_logger(__name__)

_RISK_WEIGHT = {
    RiskLevel.NONE: 0.0,
    RiskLevel.LOW: 0.1,
    RiskLevel.MEDIUM: 0.35,
    RiskLevel.HIGH: 0.7,
    RiskLevel.CRITICAL: 1.0,
}


@dataclass
class ToolRegistry:
    _tools: dict[str, object] = field(default_factory=dict)

    def register(self, tool: object) -> None:
        spec: ToolSpec = tool.spec  # type: ignore[attr-defined]
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = tool

    def get(self, name: str) -> object:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"no tool named {name!r}", available=sorted(self._tools))
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]  # type: ignore[attr-defined]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def by_capability(self, capability: str) -> list[ToolSpec]:
        return [s for s in self.specs() if capability in s.capabilities]


class ToolRouter:
    """Chooses among tools that can perform the same capability."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def select(
        self,
        capability: str,
        *,
        sensitivity: DataSensitivity = DataSensitivity.PRIVATE,
        allowed: list[str] | None = None,
        max_risk: RiskLevel = RiskLevel.HIGH,
        offline: bool = False,
        latency_budget_ms: int | None = None,
    ) -> ToolSpec | None:
        candidates = [
            spec
            for spec in self.registry.by_capability(capability)
            if (not allowed or spec.name in allowed)
            and _RISK_WEIGHT[spec.risk] <= _RISK_WEIGHT[max_risk]
            and spec.max_sensitivity >= sensitivity
            and not (sensitivity >= DataSensitivity.SECRET and not spec.local_only)
            and not (offline and not spec.local_only and spec.control_tier is ControlTier.API)
            and (latency_budget_ms is None or spec.latency_ms <= latency_budget_ms)
        ]
        if not candidates:
            return None
        # Lower control tier wins first (§19: an API beats a GUI click), then risk, then
        # cost, then latency. Sorting on the tuple keeps the preference order explicit.
        candidates.sort(
            key=lambda s: (int(s.control_tier), _RISK_WEIGHT[s.risk], s.cost_usd, s.latency_ms)
        )
        chosen = candidates[0]
        if len(candidates) > 1:
            log.debug(
                "tool_selected",
                capability=capability,
                chosen=chosen.name,
                over=[c.name for c in candidates[1:4]],
            )
        return chosen
