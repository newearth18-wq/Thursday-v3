"""Capability registry and agent selection (§14, §57).

The user never picks an agent. Thursday scores candidates on capability fit, device
affinity, past success, cost and risk — and asks a clarifying question rather than
delegating on a weak match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from thursday_core.logging import get_logger
from thursday_shared.errors import AgentNotFound
from thursday_shared.models import AgentSpec

log = get_logger(__name__)

#: Below this, Thursday asks instead of delegating.
MIN_SELECTION_CONFIDENCE = 0.45


@dataclass
class AgentCandidate:
    spec: AgentSpec
    score: float
    reasons: tuple[str, ...]


@dataclass
class AgentRegistry:
    _agents: dict[str, object] = field(default_factory=dict)
    #: agent name → (successes, attempts), feeding the historic-success term.
    _history: dict[str, tuple[int, int]] = field(default_factory=dict)

    def register(self, agent: object) -> None:
        spec: AgentSpec = agent.spec  # type: ignore[attr-defined]
        self._agents[spec.name] = agent

    def unregister(self, name: str) -> None:
        """Temporary agents are destroyed when their task ends (§16)."""
        self._agents.pop(name, None)

    def get(self, name: str) -> object:
        agent = self._agents.get(name)
        if agent is None:
            raise AgentNotFound(f"no agent named {name!r}", available=sorted(self._agents))
        return agent

    def has(self, name: str) -> bool:
        return name in self._agents

    def specs(self) -> list[AgentSpec]:
        return [a.spec for a in self._agents.values()]  # type: ignore[attr-defined]

    def record_outcome(self, name: str, *, success: bool) -> None:
        successes, attempts = self._history.get(name, (0, 0))
        self._history[name] = (successes + int(success), attempts + 1)

    def success_rate(self, name: str) -> float:
        successes, attempts = self._history.get(name, (0, 0))
        return 0.7 if attempts < 3 else successes / attempts  # optimistic prior, quickly corrected

    def select(
        self,
        *,
        capabilities: list[str],
        available_tools: list[str] | None = None,
        device_online: bool = True,
    ) -> AgentCandidate | None:
        candidates: list[AgentCandidate] = []
        for agent in self._agents.values():
            spec: AgentSpec = agent.spec  # type: ignore[attr-defined]
            if not capabilities:
                continue
            overlap = len(set(capabilities) & set(spec.capabilities))
            if overlap == 0:
                continue
            reasons = [f"matches {overlap}/{len(capabilities)} requested capabilities"]
            capability_match = overlap / len(capabilities)

            affinity = 1.0
            needs_device = any(t in ("open_app", "run_shell", "screenshot") for t in spec.tools)
            if needs_device and not device_online:
                affinity = 0.0
                reasons.append("requires a device but none is online")

            tool_gap = 0.0
            if available_tools is not None and spec.tools:
                missing = [t for t in spec.tools if t not in available_tools]
                if missing:
                    tool_gap = len(missing) / len(spec.tools)
                    reasons.append(f"{len(missing)} of its tools are unavailable")

            history = self.success_rate(spec.name)
            cost = min(1.0, (spec.default_budget.usd or 0.1) / 1.0)
            risk = int(spec.permission_ceiling) / 5

            score = (
                0.50 * capability_match
                + 0.20 * affinity
                + 0.15 * history
                - 0.10 * cost
                - 0.05 * risk
                - 0.20 * tool_gap
            )
            candidates.append(AgentCandidate(spec, score, tuple(reasons)))

        if not candidates:
            return None
        candidates.sort(key=lambda c: c.score, reverse=True)
        best = candidates[0]
        log.debug(
            "agent_selected",
            agent=best.spec.name,
            score=round(best.score, 3),
            over=[c.spec.name for c in candidates[1:3]],
        )
        return best if best.score >= MIN_SELECTION_CONFIDENCE else None
