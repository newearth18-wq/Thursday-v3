"""Base agent (§17).

Every agent receives a ``JobContract`` and returns an ``AgentResult`` that matches the
contract's output schema. Schema violations fail at this boundary, so the Supervisor is
never handed malformed data to judge.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from thursday_core.logging import get_logger
from thursday_core.plain import activity, friendly
from thursday_shared.errors import ThursdayError
from thursday_shared.models import AgentResult, AgentSpec, Event, JobContract

log = get_logger(__name__)


def _describe(exc: Exception) -> str:
    """Turn an exception into something the owner can be shown.

    A `ThursdayError` already carries a sentence written for a person — "Pixel is not
    trusted to control other machines" — and prefixing it with the class name turns a clear
    refusal into a stack trace. Anything else keeps the type, because for a genuine crash
    the type is most of the information there is.
    """
    if isinstance(exc, ThursdayError):
        return exc.message
    return f"{type(exc).__name__}: {exc}"


class BaseAgent(ABC):
    spec: AgentSpec

    async def run(self, contract: JobContract, ctx: Any) -> AgentResult:
        started = time.perf_counter()
        # What the owner sees is what Thursday is *doing*, not which class is doing it
        # ("กำลังค้นข้อมูล", never "ResearchAgent #84"). The agent's own name stays in the
        # structured log, where an operator wants it and a person never looks.
        await ctx.emit(
            Event(
                kind="agent.started",
                payload={
                    # `agent` is internal: world state keys `running_agents` on it and an
                    # operator wants it. `activity` is the only field a screen renders.
                    "agent": self.spec.name,
                    "activity": activity(capabilities=self.spec.capabilities),
                },
            )
        )
        try:
            result = await self.execute(contract, ctx)
        except Exception as exc:
            log.warning("agent_failed", agent=self.spec.name, error=str(exc))
            # `friendly`, not `str(exc)`. This payload reaches the UI, and the raw text is
            # where "ConnectionError ECONNREFUSED localhost:11434" arrives in front of
            # somebody who never chose to run anything on a port. The original is kept in
            # `technical`, which only Developer Options renders.
            plain = friendly(exc)
            await ctx.emit(
                Event(
                    kind="agent.failed",
                    payload={
                        "agent": self.spec.name,
                        "activity": activity(capabilities=self.spec.capabilities),
                        "error": plain.message,
                        "repairable": plain.repairable,
                        "technical": plain.technical,
                    },
                )
            )
            return AgentResult(
                agent=self.spec.name,
                ok=False,
                error=_describe(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
                spend=ctx.spend,
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
                payload={
                    "agent": self.spec.name,
                    "activity": activity(capabilities=self.spec.capabilities),
                    "ok": result.ok,
                },
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
