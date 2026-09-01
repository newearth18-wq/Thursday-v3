"""Base agent (§17).

Every agent receives a ``JobContract`` and returns an ``AgentResult`` that matches the
contract's output schema. Schema violations fail at this boundary, so the Supervisor is
never handed malformed data to judge.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from thursday.core.logging import get_logger
from thursday.shared.models import AgentResult, AgentSpec, Event, JobContract

log = get_logger(__name__)


class BaseAgent(ABC):
    spec: AgentSpec

    async def run(self, contract: JobContract, ctx: Any) -> AgentResult:
        started = time.perf_counter()
        await ctx.emit(Event(kind="agent.started", payload={"agent": self.spec.name}))
        try:
            result = await self.execute(contract, ctx)
        except Exception as exc:
            log.warning("agent_failed", agent=self.spec.name, error=str(exc))
            await ctx.emit(
                Event(kind="agent.failed", payload={"agent": self.spec.name, "error": str(exc)})
            )
            return AgentResult(
                agent=self.spec.name, ok=False, error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - started) * 1000, spend=ctx.spend,
            )

        result.duration_ms = (time.perf_counter() - started) * 1000
        result.spend = ctx.spend
        missing = self._schema_gaps(contract, result)
        if missing:
            result.ok = False
            result.error = f"output is missing required fields: {', '.join(missing)}"
        await ctx.emit(
            Event(
                kind="agent.completed" if result.ok else "agent.failed",
                payload={"agent": self.spec.name, "ok": result.ok},
            )
        )
        return result

    @abstractmethod
    async def execute(self, contract: JobContract, ctx: Any) -> AgentResult:
        """Do the work. Raise or return ``ok=False`` on failure — never fake success."""

    def _schema_gaps(self, contract: JobContract, result: AgentResult) -> list[str]:
        if not contract.output_schema or not result.ok:
            return []
        required = [k for k in contract.output_schema if not k.endswith("?")]
        return [k for k in required if k not in result.output]
